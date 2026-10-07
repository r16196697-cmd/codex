"""External reviewed artifact regressions; never activate the real Host."""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[2]
METADATA = json.loads((REPO / "tools/host-bootstrap-mcp-compatibility.json").read_text(encoding="utf-8"))


def _git(*args):
    return subprocess.check_output(["git", "-c", "safe.directory=" + REPO.as_posix(),
                                    "-C", str(REPO), *args])


def _patched_source():
    source = os.environ.get("NEXUS_REVIEWED_HOST_INSTALLER")
    if not source:
        raise unittest.SkipTest("External reviewed Bootstrap source not supplied")
    raw = Path(source).read_bytes()
    if hashlib.sha256(raw).hexdigest() != METADATA["source_artifact_sha256"]:
        raise AssertionError("Reviewed installer source hash conflict")
    patch = (REPO / METADATA["patch"]).read_bytes()
    if hashlib.sha256(patch.replace(b"\r\n", b"\n")).hexdigest() != METADATA["patch_sha256"]:
        raise AssertionError("Reviewed patch hash conflict")
    lines = patch.decode("utf-8").splitlines()
    old = next(line[1:].encode() for line in lines if line.startswith("-    mapping"))
    new = next(line[1:].encode() for line in lines if line.startswith("+    mapping"))
    if raw.count(old) != 1:
        raise AssertionError("Reviewed installer mapping conflict")
    candidate = raw.replace(old, new)
    if hashlib.sha256(candidate).hexdigest() != METADATA["artifact_sha256"]:
        raise AssertionError("Candidate installer hash conflict")
    return raw, candidate


class BootstrapDependencyTests(unittest.TestCase):
    def setUp(self):
        self.original, candidate = _patched_source()
        self.temp = tempfile.TemporaryDirectory(prefix="nexus-bootstrap-dependency-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        path = self.base / "install_nexus_host.py"
        path.write_bytes(candidate)
        spec = importlib.util.spec_from_file_location("reviewed_bootstrap_fixture", path)
        self.installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.installer)
        self.installer.PYTHON = Path(sys.executable)
        self.installer.HOST = self.base

    def _target(self, revision):
        target = self.base / revision
        target.mkdir()
        raw = _git("show", revision + ":pyproject.toml")
        (target / "pyproject.toml").write_bytes(raw)
        return target

    def test_exact_patch_and_embedded_runtime_unchanged(self):
        old, candidate = _patched_source()
        changed = candidate.replace(b', "mcp": "mcp"', b"")
        self.assertEqual(changed, old)
        diff = ("\n".join(difflib.unified_diff(old.decode().splitlines(), candidate.decode().splitlines(),
                fromfile="install_nexus_host.py", tofile="install_nexus_host.py", n=0, lineterm="")) + "\n").encode()
        self.assertEqual(hashlib.sha256(diff).hexdigest(), METADATA["artifact_diff_sha256"])
        self.assertEqual(Path(os.environ["NEXUS_REVIEWED_HOST_INSTALLER"]).read_bytes(), self.original)

    def test_dependencies_support_old_and_new_runtime(self):
        for revision in (METADATA["previous_runtime_test_sha"], METADATA["candidate_runtime_test_sha"]):
            self.installer.dependencies(self._target(revision))

    def test_wrong_exact_version_fails_closed(self):
        target = self._target(METADATA["candidate_runtime_test_sha"])
        path = target / "pyproject.toml"
        path.write_text(path.read_text(encoding="utf-8").replace("mcp==2.3.0", "mcp==0.0.0"), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, r"HOST_INSTALL_DEPENDENCY_UNAVAILABLE:mcp==0\.0\.0"):
            self.installer.dependencies(target)

    def test_import_failure_fails_closed_despite_exact_metadata(self):
        target = self._target(METADATA["candidate_runtime_test_sha"])
        package = self.base / "mcp"
        package.mkdir()
        (package / "__init__.py").write_text("raise ImportError('synthetic broken dependency')\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, r"HOST_INSTALL_DEPENDENCY_UNAVAILABLE:mcp==2\.3\.0"):
            self.installer.dependencies(target)


class DependencyClosureTests(unittest.TestCase):
    def test_canonical_lock_is_exact_complete_windows_runtime_closure(self):
        # Use distribution metadata, not another hand-maintained dependency list.
        from pip._vendor.packaging.requirements import Requirement
        from pip._vendor.packaging.utils import canonicalize_name
        requirements = [Requirement(line) for line in (REPO / "requirements.lock.txt").read_text().splitlines()
                        if line.strip() and not line.startswith("#")]
        pins = {}
        for requirement in requirements:
            if requirement.marker and not requirement.marker.evaluate():
                continue
            specs = list(requirement.specifier)
            self.assertEqual(len(specs), 1)
            self.assertEqual(specs[0].operator, "==")
            name = canonicalize_name(requirement.name)
            self.assertNotIn(name, pins)
            pins[name] = specs[0].version
            self.assertEqual(importlib.metadata.version(requirement.name), specs[0].version)
        direct = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
        self.assertEqual(direct, (REPO / "requirements.txt").read_text().splitlines())
        queue = [Requirement(item) for item in direct]
        queue.extend(Requirement(f"{name}=={version}") for name, version in pins.items())
        seen, edges = set(), []
        while queue:
            requirement = queue.pop()
            name = canonicalize_name(requirement.name)
            identity = (name, tuple(sorted(requirement.extras)))
            if identity in seen:
                continue
            seen.add(identity)
            self.assertIn(name, pins)
            self.assertTrue(requirement.specifier.contains(pins[name]))
            for raw in importlib.metadata.requires(name) or []:
                dependency = Requirement(raw)
                if dependency.marker and not any(dependency.marker.evaluate({"extra": extra})
                        for extra in {"", *requirement.extras}):
                    continue
                dep = canonicalize_name(dependency.name)
                self.assertIn(dep, pins, (name, raw))
                self.assertTrue(dependency.specifier.contains(pins[dep]), (name, raw, pins[dep]))
                edges.append({"from": name, "to": dep, "requires": raw})
                queue.append(dependency)
        self.assertEqual(pins["mcp"], "2.3.0")
        self.assertEqual(pins["mcp-types"], "2.3.0")
        if os.name == "nt":
            self.assertIn("pywin32", pins)
        receipt_dir = os.environ.get("NEXUS_BOOTSTRAP_RECEIPT_DIR")
        if receipt_dir:
            (Path(receipt_dir) / "dependency-closure.json").write_text(json.dumps({
                "python": sys.version, "platform": sys.platform, "pins": pins, "edges": edges,
                "missing": [], "version_conflicts": []}, indent=2), encoding="utf-8")


@unittest.skipUnless(os.name == "nt", "Reviewed Bootstrap is Windows-only")
class DisposableHostUpgradeTests(unittest.TestCase):
    def test_real_installer_upgrade_rollback_shadow_and_fixture_mcp(self):
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters
        from tests.integration.test_read_plane import ReadPlaneFixture, commitment

        _, candidate = _patched_source()
        interpreter = os.environ.get("NEXUS_BOOTSTRAP_TEST_PYTHON")
        if not interpreter:
            self.skipTest("Disposable locked Python environment not supplied")
        self.assertEqual(Path(interpreter).resolve(), Path(sys.executable).resolve())
        with tempfile.TemporaryDirectory(prefix="nexus-disposable-host-") as temp:
            base = Path(temp)
            source = base / "install_nexus_host.py"
            source.write_bytes(candidate)
            spec = importlib.util.spec_from_file_location("isolated_reviewed_installer", source)
            installer = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(installer)
            installer.REPOSITORY = REPO
            installer.HOST = base / "Nexus"
            installer.PYTHON = Path(interpreter)
            # Test-only environment constants; artifact bytes remain exact.
            python_lines = [line for line in installer.BOOTSTRAP.splitlines() if line.startswith("PYTHON = ")]
            self.assertEqual(len(python_lines), 1)
            original_python = python_lines[0]
            installer.BOOTSTRAP = installer.BOOTSTRAP.replace(original_python,
                "PYTHON = Path(" + repr(interpreter) + ")")
            installer.LAUNCHER = '@echo off\r\n"' + interpreter + '" "%~dp0nexus-bootstrap.py" %*\r\nexit /b %ERRORLEVEL%\r\n'
            environment = dict(os.environ)
            for key in ("PYTHONPATH", "PYTHONHOME"):
                environment.pop(key, None)
            environment.update(LOCALAPPDATA=str(base), PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
            transcripts = []
            with mock.patch.dict(os.environ, environment, clear=True), \
                 mock.patch.object(installer, "update_user_path") as path_update:
                for revision in (METADATA["previous_runtime_test_sha"], METADATA["candidate_runtime_test_sha"]):
                    installer.SHA = revision
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(installer.main(), 0, output.getvalue())
                    transcript = output.getvalue()
                    transcripts.append(transcript)
                    for marker in ("SNAPSHOT_VERIFIED", "DEPENDENCIES_VERIFIED", "CANDIDATE_SMOKE_VERIFIED", "POST_SWITCH_VERIFIED"):
                        self.assertIn(marker, transcript)
                self.assertEqual(path_update.call_count, 2)
                host = installer.HOST
                pointer = host / "active-runtime.json"
                active = pointer.read_bytes()
                self.assertEqual(json.loads(active)["commit_sha"], METADATA["candidate_runtime_test_sha"])
                self.assertEqual(json.loads((host / "previous-runtime.json").read_bytes())["commit_sha"], METADATA["previous_runtime_test_sha"])
                launcher = host / "bin" / "nexus.cmd"
                def launch(*args, cwd=base, env=environment):
                    return subprocess.run([os.environ["COMSPEC"], "/d", "/c", str(launcher), *args],
                                          cwd=cwd, env=env, capture_output=True, timeout=60)
                self.assertIn(b"experience", launch("--help").stdout)
                mcp_help = launch("mcp", "serve", "--help")
                self.assertEqual(mcp_help.returncode, 0, mcp_help.stderr)
                self.assertIn(b"--reader-profile", mcp_help.stdout)
                # Poison cwd with fake source; installed bootstrap removes it.
                shadow = base / "shadow"
                (shadow / "adapters").mkdir(parents=True)
                (shadow / "adapters" / "__init__.py").write_text("raise AssertionError('wrong import root')")
                probe = launch("--host-bootstrap-self-test", cwd=shadow)
                self.assertEqual(probe.returncode, 0, probe.stderr)
                observed = json.loads(probe.stdout)
                self.assertTrue(Path(observed["cli"]).is_relative_to(host / "runtimes" / installer.SHA))
                # Real rollback path after a pointer switch, without bypassing
                # snapshot/dependency/candidate checks.
                real_run = installer.subprocess.run
                def fail_post_switch(command, **kwargs):
                    if "--host-bootstrap-self-test" in command:
                        return subprocess.CompletedProcess(command, 1, b"", b"synthetic interruption")
                    return real_run(command, **kwargs)
                installer.SHA = METADATA["previous_runtime_test_sha"]
                logs = []
                with mock.patch.object(installer.subprocess, "run", fail_post_switch):
                    with self.assertRaisesRegex(RuntimeError, "HOST_INSTALL_POST_SWITCH_IMPORT_FAILED"):
                        installer.install(logs.append)
                self.assertIn("ACTIVE_POINTER_ROLLED_BACK", logs)
                self.assertEqual(pointer.read_bytes(), active)
                installer.SHA = METADATA["candidate_runtime_test_sha"]
                target = host / "runtimes" / installer.SHA / "pyproject.toml"
                original = target.read_bytes()
                target.write_bytes(original + b"\n# synthetic corruption\n")
                try:
                    with self.assertRaisesRegex(RuntimeError, "HOST_INSTALL_SNAPSHOT_CONFLICT"):
                        installer.materialize()
                    self.assertEqual(pointer.read_bytes(), active)
                finally:
                    target.write_bytes(original)
                fixture = ReadPlaneFixture()
                fixture.private_local = True
                self.addCleanup(fixture.doCleanups)
                fixture.setUp()
                # MCP children receive an explicit fixture registry below;
                # do not leave a nested environment patch beyond this scope.
                fixture.env_patch.stop()
                before = commitment(fixture.fixture.data_root)
                journal = (fixture.fixture.journal_path.read_bytes(), fixture.fixture.journal_path.stat().st_mtime_ns)
                async def smoke():
                    for profile in (None, "local-no-egress"):
                        args = ["/d", "/c", str(launcher), "mcp", "serve"]
                        if profile:
                            args += ["--reader-profile", profile]
                        async with Client(StdioServerParameters(command=environment["COMSPEC"], args=args,
                                env={**environment, **fixture.environment}, cwd=fixture.nested)) as client:
                            self.assertEqual(client.server_info.name, "nexus")
                            self.assertEqual(len((await client.list_tools()).tools), 5)
                            result = await client.call_tool("nexus_read_evidence", {"evidence_ref": fixture.evidence_id})
                            if profile is None:
                                self.assertTrue(result.is_error)
                                self.assertEqual(result.structured_content["reason"], "READ_PLANE_DENIED")
                                self.assertNotIn("data", result.structured_content)
                            else:
                                self.assertFalse(result.is_error, result)
                                self.assertEqual(result.structured_content["data"]["payload_content_trust"], "UNKNOWN")
                asyncio.run(smoke())
                self.assertEqual(before, commitment(fixture.fixture.data_root))
                self.assertEqual(journal, (fixture.fixture.journal_path.read_bytes(), fixture.fixture.journal_path.stat().st_mtime_ns))
                self.assertEqual(source.read_bytes(), candidate)
                receipt_dir = os.environ.get("NEXUS_BOOTSTRAP_RECEIPT_DIR")
                if receipt_dir:
                    (Path(receipt_dir) / "disposable-upgrade.log").write_text("\n".join(transcripts), encoding="utf-8")
                    (Path(receipt_dir) / "disposable-smoke.json").write_text(json.dumps({
                        "old_runtime": METADATA["previous_runtime_test_sha"], "new_runtime": METADATA["candidate_runtime_test_sha"],
                        "generic_reader": "READ_PLANE_DENIED", "fixture_no_egress_reader": "OK",
                        "fixture_commitment_unchanged": True, "rollback_verified": True,
                        "snapshot_conflict_rejected": True, "shadow_protection_verified": True,
                        "pythonpath": None, "real_user_path_modified": False, "production_access": False}, indent=2), encoding="utf-8")
