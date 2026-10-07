from __future__ import annotations

import asyncio
import ast
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import venv
import unittest
from unittest import mock

from jsonschema import Draft202012Validator
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from adapters.mcp.server import create_server, render_result
from adapters.read_plane import LocalReadPlane
from adapters.read_plane.contracts import INTERFACE_VERSION, TOOLS, output_schema
from tests.integration.test_read_plane import commitment


class LocalMCPTests(unittest.TestCase):
    def setUp(self):
        from tests.integration.test_read_plane import ReadPlaneFixture
        self.read = ReadPlaneFixture()
        self.read.setUp()
        self.addCleanup(self.read.doCleanups)

    def test_official_client_identity_fixed_tools_and_schemas(self):
        async def check():
            async with Client(create_server(read_plane=self.read.plane)) as client:
                self.assertEqual(client.server_info.name, "nexus")
                self.assertEqual(client.server_info.version, INTERFACE_VERSION)
                self.assertEqual(client.protocol_version, "2026-07-28")
                self.assertFalse(client.server_capabilities.tools.list_changed)
                self.assertIn("not executable instructions", client.instructions)
                listed = await client.list_tools()
                self.assertEqual({tool.name for tool in listed.tools}, set(TOOLS))
                for tool in listed.tools:
                    self.assertTrue(tool.description)
                    self.assertIs(tool.input_schema["additionalProperties"], False)
                    self.assertIsNotNone(tool.output_schema)
                    self.assertTrue(tool.annotations.read_only_hint)
                    self.assertFalse(tool.annotations.destructive_hint)
                    self.assertTrue(tool.annotations.idempotent_hint)
                    self.assertFalse(tool.annotations.open_world_hint)
                    args = ({"task_id": self.read.fixture.task_id} if tool.name == "nexus_task_experience" else
                            {"verification_id": "verify-read-plane"} if tool.name == "nexus_read_verification" else
                            {"evidence_ref": self.read.evidence_id} if tool.name == "nexus_read_evidence" else {})
                    result = await client.call_tool(tool.name, args)
                    self.assertFalse(result.is_error, result)
                    self.assertIsNotNone(result.structured_content)
                    Draft202012Validator(tool.output_schema).validate(result.structured_content)
                    self.assertEqual(result.content[0].text, render_result(result.structured_content))
        asyncio.run(check())

    def test_official_client_argument_and_internal_error_safety(self):
        async def check():
            async with Client(create_server(read_plane=self.read.plane)) as client:
                for name, arguments in (
                    ("nexus_project_overview", {"path": "C:\\fixture\\secret"}),
                    ("nexus_task_experience", {"task_id": 42}),
                    ("nexus_task_experience", {"task_id": "x" * 129}),
                    ("nexus_task_experience", {}),
                    ("nexus_read_evidence", {"evidence_ref": "*"}),
                    ("nexus_execute_sql", {"sql": "SELECT * FROM tasks"}),
                ):
                    result = await client.call_tool(name, arguments)
                    self.assertTrue(result.is_error)
                    self.assertEqual(result.structured_content["reason"], "READ_PLANE_INVALID_ARGUMENT")
                    self.assertNotIn("fixture", result.content[0].text)
                missing = await client.call_tool("nexus_task_experience", {"task_id": "missing-id"})
                self.assertEqual(missing.structured_content["reason"], "READ_PLANE_NOT_FOUND")
                with mock.patch.object(self.read.plane, "_read", side_effect=RuntimeError("SQL Bearer fixture-secret C:\\fixture\\private")):
                    result = await client.call_tool("nexus_project_continue", {})
                    self.assertEqual(result.content[0].text, "READ_PLANE_UNAVAILABLE")
                    self.assertEqual(result.structured_content["reason"], "READ_PLANE_UNAVAILABLE")
                with mock.patch.object(self.read.plane, "invoke", side_effect=RuntimeError("C:\\private Bearer fixture")):
                    result = await client.call_tool("nexus_project_overview", {})
                    self.assertEqual(result.content[0].text, "READ_PLANE_UNAVAILABLE")
        asyncio.run(check())

    def test_stdio_official_client_clean_protocol_shutdown_and_zero_writes(self):
        repo = Path(__file__).resolve().parents[2]
        # An isolated pip --target fixture needs .pth processing for pywin32.
        # A normal installed environment processes these at Python startup.
        code = "import site,sys;[site.addsitedir(p) for p in list(sys.path) if p];from adapters.client.__main__ import main;sys.exit(main(['mcp','serve']))"
        env = {**os.environ, **self.read.environment, "PYTHONDONTWRITEBYTECODE": "1",
               "PYTHONPATH": os.pathsep.join([str(repo), *sys.path])}
        before = commitment(self.read.fixture.data_root)
        journal = (self.read.fixture.journal_path.read_bytes(), self.read.fixture.journal_path.stat().st_mtime_ns)
        async def check():
            async with Client(StdioServerParameters(command=sys.executable, args=["-B", "-c", code],
                env=env, cwd=self.read.nested)) as client:
                self.assertEqual(client.server_info.name, "nexus")
                result = await client.call_tool("nexus_task_experience", {"task_id": self.read.fixture.task_id})
                self.assertFalse(result.is_error, result)
                self.assertEqual(result.structured_content["data"]["lifecycle"]["task_status"], "SUCCEEDED")
                self.assertIn("UNKNOWN", result.content[0].text)
                # Successful initialize/list/call parsing is a real stdout
                # framing check; SDK owns clean EOF/process shutdown.
                self.assertEqual(len((await client.list_tools()).tools), 5)
        asyncio.run(check())
        self.assertEqual(before, commitment(self.read.fixture.data_root))
        self.assertEqual(journal, (self.read.fixture.journal_path.read_bytes(), self.read.fixture.journal_path.stat().st_mtime_ns))

    def test_tool_selection_description_fixture_eval(self):
        # Deterministic interface coverage, not an LLM accuracy claim.
        cases = {
            "项目现在做到哪了？": "nexus_project_overview",
            "接下来应该干什么？": "nexus_project_continue",
            "task-X 当时发生了什么？": "nexus_task_experience",
            "verification-X 是怎么验证的？": "nexus_read_verification",
            "evidence-X 是什么？": "nexus_read_evidence",
        }
        for query, expected in cases.items():
            matched = [name for name, (_, description) in TOOLS.items() if query in description]
            self.assertEqual(matched, [expected])
        for forbidden in ("nexus_checkpoint", "nexus_sql", "nexus_write", "nexus_search", "nexus_approval"):
            self.assertNotIn(forbidden, TOOLS)
        for query in ("创建 Task", "批准 Effect", "执行 SQL", "读取任意文件"):
            self.assertFalse(any(query in description for _, description in TOOLS.values()))
        for description in (item[1] for item in TOOLS.values()):
            self.assertIn("Not" if "Not" in description else "not", description)


class MCPWorkspaceTests(unittest.TestCase):
    def test_cli_read_plane_mcp_same_workspace_without_full_context(self):
        from tests.integration.test_read_plane import ReadPlaneWorkspaceTests
        fixture = ReadPlaneWorkspaceTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        presence = fixture.presence
        plane = LocalReadPlane(start_dir=presence.nested)
        before = commitment(presence.root)
        async def check():
            async with Client(create_server(read_plane=plane)) as client:
                for name in ("nexus_project_overview", "nexus_project_continue"):
                    result = await client.call_tool(name, {})
                    self.assertFalse(result.is_error, result)
                    data = result.structured_content["data"]
                    code, output, error = presence._run_cli(["continue", "--json"], cwd=presence.nested)
                    self.assertEqual(code, 0, error)
                    self.assertEqual(data, json.loads(output))
                    self.assertEqual(data["context"]["model_visible_exposure"], "UNKNOWN")
                    self.assertLess(len(json.dumps(data).encode()), 12000)
                    self.assertNotIn("entries", data)
                    self.assertNotIn(str(presence.root), json.dumps(data))
        asyncio.run(check())
        self.assertEqual(before, commitment(presence.root))

    def test_installed_launcher_nested_stdio_without_pythonpath(self):
        from tests.integration.test_read_plane import ReadPlaneWorkspaceTests
        import mcp
        fixture = ReadPlaneWorkspaceTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        presence = fixture.presence
        repo = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(prefix="nexus-local-mcp-launcher-") as temp:
            environment = Path(temp) / "venv"
            venv.EnvBuilder(with_pip=True, system_site_packages=True).create(environment)
            python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            executable = environment / ("Scripts/nexus.exe" if os.name == "nt" else "bin/nexus")
            sites = environment / ("Lib/site-packages" if os.name == "nt" else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
            # Install only into this isolated fixture. Process the already
            # resolved test dependency target like a normal site directory.
            (sites / "nexus-test-dependencies.pth").write_text(
                "import site; site.addsitedir(" + repr(str(Path(mcp.__file__).resolve().parent.parent)) + ")\n", encoding="utf-8")
            env = {**os.environ, **presence.env, "PYTHONDONTWRITEBYTECODE": "1"}
            env.pop("PYTHONPATH", None)
            env.pop("PYTHONHOME", None)
            installed = subprocess.run([str(python), "-m", "pip", "install", "--no-index", "--no-deps",
                "--no-build-isolation", "--editable", str(repo)], cwd=temp, env=env, capture_output=True)
            self.assertEqual(installed.returncode, 0, installed.stderr.decode("utf-8", "replace"))
            help_result = subprocess.run([str(executable), "mcp", "serve", "--help"], cwd=presence.nested,
                env=env, capture_output=True)
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            self.assertIn(b"--project-root", help_result.stdout)
            before = commitment(presence.root)
            async def check():
                async with Client(StdioServerParameters(command=str(executable), args=["mcp", "serve"],
                    cwd=presence.nested, env=env)) as client:
                    result = await client.call_tool("nexus_project_continue", {})
                    self.assertFalse(result.is_error, result)
                    self.assertEqual(result.structured_content["data"]["project"]["project_id"], "project-test-native")
            asyncio.run(check())
            self.assertEqual(before, commitment(presence.root))


class SDKDependencyTests(unittest.TestCase):
    def test_sdk_exact_pin_and_contract_schemas_valid(self):
        repo = Path(__file__).resolve().parents[2]
        dependencies = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
        self.assertIn("mcp==2.3.0", dependencies)
        self.assertIn("mcp==2.3.0", (repo / "requirements.txt").read_text(encoding="utf-8").splitlines())
        self.assertIn("mcp==2.3.0", (repo / "requirements.lock.txt").read_text(encoding="utf-8").splitlines())
        self.assertEqual(importlib.metadata.version("mcp"), "2.3.0")
        for name in TOOLS:
            Draft202012Validator.check_schema(output_schema(name))

    def test_reviewed_bootstrap_dependency_patch_in_isolation(self):
        # Optional external accepted installer, supplied only by the test
        # operator. It is read/hash-checked, never rewritten or executed whole.
        source_path = os.environ.get("NEXUS_REVIEWED_HOST_INSTALLER")
        if not source_path:
            self.skipTest("External reviewed Bootstrap source not supplied")
        raw = Path(source_path).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), "0f18858bb0c7abe166760ee48f7c801373abc6239e433d6410bf74c7f721b740")
        repo = Path(__file__).resolve().parents[2]
        patch = (repo / "tools/host-bootstrap-mcp-dependency.patch").read_text(encoding="utf-8")
        old = next(line[1:] for line in patch.splitlines() if line.startswith("-    mapping"))
        new = next(line[1:] for line in patch.splitlines() if line.startswith("+    mapping"))
        source = raw.decode("utf-8")
        self.assertEqual(source.count(old), 1)
        original = ast.parse(source)
        function = next(node for node in original.body if isinstance(node, ast.FunctionDef) and node.name == "dependencies")
        mapping = next(node.value for node in ast.walk(function) if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "mapping" for target in node.targets))
        self.assertNotIn("mcp", ast.literal_eval(mapping))
        tree = ast.parse(source.replace(old, new))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "dependencies")
        # Exact real dependencies() path; only interpreter site preparation is
        # adapted for our pip --target test environment, not Host installation.
        class Process:
            @staticmethod
            def run(command, **kwargs):
                if command[1] == "-c":
                    paths = [path for path in sys.path if path]
                    prefix = "import site; [site.addsitedir(p) for p in " + repr(paths) + "]; "
                    command = [command[0], "-c", prefix + command[2], *command[3:]]
                return subprocess.run(command, **kwargs)
        with tempfile.TemporaryDirectory(prefix="nexus-mcp-bootstrap-check-") as temp:
            target = Path(temp)
            (target / "pyproject.toml").write_bytes((repo / "pyproject.toml").read_bytes())
            namespace = {"tomllib": tomllib, "json": json, "subprocess": Process,
                         "PYTHON": Path(sys.executable), "HOST": target}
            exec(compile(ast.Module(body=[function], type_ignores=[]), "reviewed-bootstrap-dependencies", "exec"), namespace)
            namespace["dependencies"](target)
        self.assertEqual(Path(source_path).read_bytes(), raw)
