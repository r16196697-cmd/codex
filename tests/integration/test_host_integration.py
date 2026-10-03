from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import contextlib
import io

from adapters.client.codex_host import (
    CODEX_SESSION_START_SOURCES,
    CodexHostError,
    _EXPECTED_REGISTRATION,
    codex_host_status,
    codex_hooks_path,
    install_codex_host,
    normalize_session_start,
    parse_session_start_json,
    session_start_output,
    uninstall_codex_host,
)
from adapters.client.host_integration import (
    HostSessionStartInput,
    NexusHostContext,
    build_host_context,
)
from adapters.client.__main__ import main


def _manifest(root: Path, *, contents: str | None = None) -> Path:
    nexus = root / ".nexus"
    nexus.mkdir(parents=True)
    path = nexus / "project.json"
    path.write_text(contents or json.dumps({
        "schema_id": "nexus.project_manifest", "schema_version": 1,
        "project_id": "project-host-test",
    }), encoding="utf-8")
    return path


class HostContractTests(unittest.TestCase):
    def test_host_neutral_contract_uses_shared_workspace_builder(self):
        with tempfile.TemporaryDirectory(prefix="nexus-host-contract-") as temp:
            root = Path(temp) / "repo"
            nested = root / "src" / "nested"
            nested.mkdir(parents=True)
            _manifest(root)
            binding = {
                "status": "PROJECT_LOCATED", "project_id": "project-host-test",
                "instance_id": "instance-test", "data_root": "host-only",
            }
            workspace = {
                "project": {"project_id": "project-host-test"},
                "current_state": {"status": "AVAILABLE", "ref": "state-r2"},
                "freshness": {"basis": "VERIFIED_LATEST_CONTEXT_PACK"},
                "context": {"model_visible_exposure": "UNKNOWN"},
            }
            request = HostSessionStartInput(
                cwd=str(nested), host_name="future-host", host_version="1", session_id="s1",
                source="RESUMED_SESSION",
            )
            locator_calls = []
            workspace_calls = []

            def locator(**kwargs):
                locator_calls.append(kwargs)
                return binding

            def builder(**kwargs):
                workspace_calls.append(kwargs)
                return workspace

            result = build_host_context(request, locator=locator, workspace_builder=builder)
            self.assertIsInstance(result, NexusHostContext)
            self.assertEqual(result.to_document()["schema_version"], 1)
            self.assertEqual(result.project_id, "project-host-test")
            self.assertEqual(result.state_ref, "state-r2")
            self.assertEqual(result.exposure_status, "UNKNOWN")
            self.assertEqual(locator_calls, [{"start_dir": root.resolve()}])
            self.assertEqual(workspace_calls[0]["locator"](), binding)

    def test_codex_event_normalization_and_strict_json(self):
        normalized = normalize_session_start({
            "cwd": "C:/repo/nested", "source": "compact", "session_id": "s-1",
            "codex_version": "0.1", "transcript_path": "private-and-ignored",
        })
        self.assertEqual(normalized, HostSessionStartInput(
            cwd="C:/repo/nested", host_name="codex", host_version="0.1",
            session_id="s-1", source="COMPACTED_SESSION",
        ))
        self.assertEqual(CODEX_SESSION_START_SOURCES, ("startup", "resume", "clear", "compact"))
        self.assertIsNone(normalize_session_start({"cwd": "C:/repo", "source": "unknown"}))
        self.assertIsNone(parse_session_start_json('{"cwd":"a","cwd":"b"}'))
        self.assertIsNone(parse_session_start_json('{"cwd":"a","n":NaN}'))
        self.assertIsNone(parse_session_start_json("[]"))

    def test_non_nexus_project_is_fast_noop_without_registry_access(self):
        with tempfile.TemporaryDirectory(prefix="nexus-host-noop-") as temp:
            with mock.patch("adapters.client.host_integration.locate_project",
                            side_effect=AssertionError("registry must not be resolved")), \
                 mock.patch("adapters.client.host_integration.build_project_workspace",
                            side_effect=AssertionError("Nexus storage must not be opened")):
                output = session_start_output({"cwd": temp, "source": "startup"})
            self.assertEqual(output, "")

    def test_kernel_has_no_dependency_on_host_adapter_modules(self):
        repository = Path(__file__).resolve().parents[2]
        for source in (repository / "kernel").rglob("*.py"):
            text = source.read_text(encoding="utf-8")
            self.assertNotIn("adapters.client.host_integration", text, str(source))
            self.assertNotIn("adapters.client.codex_host", text, str(source))
        generic_source = (repository / "adapters" / "client" / "host_integration.py").read_text(encoding="utf-8").lower()
        for vendor_term in ("codex", "claude", "deepseek", "hooks.json"):
            self.assertNotIn(vendor_term, generic_source)

    def test_project_repository_has_no_second_codex_delivery_registration(self):
        repository = Path(__file__).resolve().parents[2]
        self.assertFalse((repository / ".codex" / "hooks.json").exists())
        self.assertFalse((repository / ".codex" / "hooks" / "nexus_session_start.py").exists())

    def test_malformed_or_unbound_project_is_fail_soft_without_disclosure(self):
        with tempfile.TemporaryDirectory(prefix="nexus-host-fail-soft-") as temp:
            root = Path(temp) / "malformed"
            root.mkdir()
            _manifest(root, contents='{"schema_id":"nexus.project_manifest","schema_version":1,"project_id":"private-path"')
            output = session_start_output({"cwd": str(root / ".nexus")})
            self.assertEqual(output, "")
            self.assertNotIn(temp, output)

            unbound = Path(temp) / "unbound"
            unbound.mkdir()
            _manifest(unbound)
            with mock.patch.dict(os.environ, {"NEXUS_PROJECT_REGISTRY": str(Path(temp) / "missing-registry.json")}):
                output = session_start_output({"cwd": str(unbound), "source": "resume"})
            self.assertEqual(output, "")
            self.assertNotIn(temp, output)


class CodexHostConfigTests(unittest.TestCase):
    def setUp(self):
        self.launcher_patch = mock.patch(
            "adapters.client.codex_host.shutil.which", return_value="nexus.exe",
        )
        self.launcher_patch.start()
        self.addCleanup(self.launcher_patch.stop)
        self.temp = tempfile.TemporaryDirectory(prefix="nexus-codex-host-config-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.path = self.base / "codex-home" / "hooks.json"
        self.unrelated = {
            "type": "command", "command": "echo existing", "timeout": 7,
        }
        self.initial_document = {
            "featureFlags": {"keep": True},
            "hooks": {
                "Notification": [{"matcher": "*", "hooks": [self.unrelated]}],
                "SessionStart": [{"matcher": "startup", "hooks": [{
                    "type": "command", "command": "echo unrelated-session-hook",
                }]}],
            },
        }
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps(self.initial_document), encoding="utf-8")

    def _confirm(self, expected, summary):
        self.assertEqual(expected, "INSTALL NEXUS HOST ADAPTER CODEX")
        self.assertEqual(summary["command"], "nexus hook session-start")
        self.assertTrue(summary["read_only"])
        self.assertNotIn(str(self.base), json.dumps(summary))
        return True

    def test_install_preserves_unrelated_hooks_and_exact_replay_does_not_rewrite(self):
        first = install_codex_host(config_path=self.path, confirmation=self._confirm)
        self.assertEqual(first["status"], "HOST_ADAPTER_INSTALLED")
        document = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(document["featureFlags"], {"keep": True})
        self.assertEqual(document["hooks"]["Notification"][0]["hooks"][0], self.unrelated)
        self.assertEqual(document["hooks"]["SessionStart"][0], self.initial_document["hooks"]["SessionStart"][0])
        self.assertEqual(document["hooks"]["SessionStart"][1], _EXPECTED_REGISTRATION)
        command = _EXPECTED_REGISTRATION["hooks"][0]["command"]
        self.assertEqual(command, "nexus hook session-start")
        self.assertNotIn(".codex", command)
        self.assertNotIn("nexus_session_start.py", command)
        self.assertEqual(_EXPECTED_REGISTRATION["hooks"][0]["commandWindows"], command)
        self.assertEqual(codex_host_status(config_path=self.path)["status"], "INSTALLED_TRUST_UNKNOWN")

        content = self.path.read_bytes()
        mtime = self.path.stat().st_mtime_ns
        lock_mtime = self.path.with_name(self.path.name + ".lock").stat().st_mtime_ns
        replay = install_codex_host(config_path=self.path, confirmation=lambda *_: self.fail("replay asked again"))
        self.assertEqual(replay, {"status": "HOST_ADAPTER_ALREADY_INSTALLED", "host": "codex", "replayed": True})
        self.assertEqual(self.path.read_bytes(), content)
        self.assertEqual(self.path.stat().st_mtime_ns, mtime)
        self.assertEqual(self.path.with_name(self.path.name + ".lock").stat().st_mtime_ns, lock_mtime)

    def test_denied_confirmation_has_zero_config_and_lock_mutation(self):
        before = {item.relative_to(self.base).as_posix() for item in self.base.rglob("*")}
        with self.assertRaises(CodexHostError) as caught:
            install_codex_host(config_path=self.path, confirmation=lambda *_: False)
        self.assertEqual(caught.exception.reason_code, "HOST_HOOK_CONFIRMATION_DENIED")
        after = {item.relative_to(self.base).as_posix() for item in self.base.rglob("*")}
        self.assertEqual(before, after)
        self.assertEqual(self.path.read_text(encoding="utf-8"), json.dumps(self.initial_document))

    def test_install_refuses_missing_launcher_before_confirmation_or_mutation(self):
        before = {item.relative_to(self.base).as_posix() for item in self.base.rglob("*")}
        with mock.patch("adapters.client.codex_host.shutil.which", return_value=None):
            with mock.patch.dict(os.environ, {"PATH": str(self.base / "empty-path")}):
                with mock.patch("adapters.client.codex_host.sys.argv", ["python"]):
                    with mock.patch("adapters.client.codex_host.sys.executable", "python.exe"):
                        with mock.patch("adapters.client.codex_host.sysconfig.get_path", return_value=str(self.base / "empty-scripts")):
                            with self.assertRaises(CodexHostError) as caught:
                                install_codex_host(config_path=self.path, confirmation=lambda *_: self.fail("must fail before confirmation"))
        self.assertEqual(caught.exception.reason_code, "NEXUS_LAUNCHER_UNAVAILABLE")
        after = {item.relative_to(self.base).as_posix() for item in self.base.rglob("*")}
        self.assertEqual(before, after)

    def test_installed_console_script_counts_as_launcher_when_path_lookup_is_stale(self):
        executable = self.base / "Scripts" / "nexus.exe"
        executable.parent.mkdir()
        executable.write_bytes(b"fixture")
        with mock.patch("adapters.client.codex_host.shutil.which", return_value=None):
            with mock.patch("adapters.client.codex_host.sys.argv", [str(executable)]):
                result = install_codex_host(config_path=self.path, confirmation=self._confirm)
        self.assertEqual(result["status"], "HOST_ADAPTER_INSTALLED")

    @unittest.skipUnless(os.name == "nt", "Windows executable lookup semantics")
    def test_windows_path_exe_lookup_does_not_depend_on_shutil_x_ok(self):
        executable = self.base / "Scripts" / "nexus.exe"
        executable.parent.mkdir()
        executable.write_bytes(b"fixture")
        with mock.patch("adapters.client.codex_host.shutil.which", return_value=None):
            with mock.patch.dict(os.environ, {"PATH": str(executable.parent)}):
                with mock.patch("adapters.client.codex_host.sys.argv", ["python"]):
                    with mock.patch("adapters.client.codex_host.sys.executable", "python.exe"):
                        result = install_codex_host(config_path=self.path, confirmation=self._confirm)
        self.assertEqual(result["status"], "HOST_ADAPTER_INSTALLED")

    def test_installed_user_console_script_is_found_from_python_module_invocation(self):
        scripts = self.base / "Scripts"
        scripts.mkdir()
        (scripts / "nexus.exe").write_bytes(b"fixture")
        with mock.patch("adapters.client.codex_host.shutil.which", return_value=None):
            with mock.patch("adapters.client.codex_host.sys.argv", ["__main__.py"]):
                with mock.patch("adapters.client.codex_host.sys.executable", "python.exe"):
                    with mock.patch("adapters.client.codex_host.sysconfig.get_path", return_value=str(scripts)):
                        result = install_codex_host(config_path=self.path, confirmation=self._confirm)
        self.assertEqual(result["status"], "HOST_ADAPTER_INSTALLED")

    def test_confirmation_sees_latest_unrelated_config_before_atomic_write(self):
        newer_unrelated = {"hooks": {"Notification": [{
            "matcher": "*", "hooks": [{"type": "command", "command": "echo concurrent"}],
        }]}}

        def confirm(expected, _summary):
            self.assertEqual(expected, "INSTALL NEXUS HOST ADAPTER CODEX")
            self.path.write_text(json.dumps(newer_unrelated), encoding="utf-8")
            return True

        install_codex_host(config_path=self.path, confirmation=confirm)
        result = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(result["hooks"]["Notification"], newer_unrelated["hooks"]["Notification"])
        self.assertEqual(result["hooks"]["SessionStart"][0], _EXPECTED_REGISTRATION)

    def test_existing_conflicting_nexus_registration_fails_closed(self):
        document = {"hooks": {"SessionStart": [{
            "matcher": "startup", "hooks": [{"type": "command", "command": "nexus hook session-start --other"}],
        }]}}
        self.path.write_text(json.dumps(document), encoding="utf-8")
        before = self.path.read_bytes()
        self.assertEqual(codex_host_status(config_path=self.path)["status"], "CONFLICT")
        with self.assertRaises(CodexHostError) as caught:
            install_codex_host(config_path=self.path, confirmation=self._confirm)
        self.assertEqual(caught.exception.reason_code, "CODEX_HOOK_CONFIG_CONFLICT")
        self.assertEqual(self.path.read_bytes(), before)

    def test_duplicate_or_malformed_json_reports_conflict_without_mutation(self):
        self.path.write_text('{"hooks":{},"hooks":{}}', encoding="utf-8")
        before = self.path.read_bytes()
        self.assertEqual(codex_host_status(config_path=self.path)["status"], "CONFLICT")
        with self.assertRaises(CodexHostError):
            install_codex_host(config_path=self.path, confirmation=self._confirm)
        self.assertEqual(self.path.read_bytes(), before)

    def test_uninstall_removes_only_owned_registration_and_replay_is_harmless(self):
        install_codex_host(config_path=self.path, confirmation=self._confirm)
        result = uninstall_codex_host(
            config_path=self.path,
            confirmation=lambda expected, _summary: expected == "UNINSTALL NEXUS HOST ADAPTER CODEX",
        )
        self.assertEqual(result["status"], "HOST_ADAPTER_UNINSTALLED")
        document = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(document["hooks"]["Notification"][0]["hooks"][0], self.unrelated)
        self.assertEqual(document["hooks"]["SessionStart"], self.initial_document["hooks"]["SessionStart"])
        content = self.path.read_bytes()
        mtime = self.path.stat().st_mtime_ns
        replay = uninstall_codex_host(config_path=self.path, confirmation=lambda *_: self.fail("absent uninstall asked"))
        self.assertEqual(replay["status"], "HOST_ADAPTER_NOT_INSTALLED")
        self.assertEqual(self.path.read_bytes(), content)
        self.assertEqual(self.path.stat().st_mtime_ns, mtime)

    def test_concurrent_install_serializes_and_never_duplicates(self):
        def confirmed(expected, _summary):
            return expected == "INSTALL NEXUS HOST ADAPTER CODEX"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda _index: install_codex_host(config_path=self.path, confirmation=confirmed),
                range(2),
            ))
        self.assertEqual({item["status"] for item in results}, {
            "HOST_ADAPTER_INSTALLED", "HOST_ADAPTER_ALREADY_INSTALLED",
        })
        document = json.loads(self.path.read_text(encoding="utf-8"))
        managed = [row for row in document["hooks"]["SessionStart"] if row == _EXPECTED_REGISTRATION]
        self.assertEqual(len(managed), 1)

    def test_codex_home_path_precedence(self):
        self.assertEqual(
            codex_hooks_path(environ={"CODEX_HOME": str(self.base / "custom")}),
            (self.base / "custom" / "hooks.json").resolve(),
        )
        self.assertEqual(
            codex_hooks_path(environ={}, home=self.base),
            (self.base / ".codex" / "hooks.json").resolve(),
        )

    def test_cli_host_status_and_non_tty_install_never_mutates_config(self):
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.path.parent)}):
            output, errors = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                status_code = main(["host", "status", "codex", "--json"])
            self.assertEqual(status_code, 0, errors.getvalue())
            self.assertEqual(json.loads(output.getvalue())["status"], "NOT_INSTALLED")

            before = {item.relative_to(self.base).as_posix() for item in self.base.rglob("*")}
            output, errors = io.StringIO(), io.StringIO()
            with mock.patch("sys.stdin", io.StringIO("")), \
                 mock.patch("sys.stdout", output), mock.patch("sys.stderr", errors):
                install_code = main(["host", "install", "codex"])
            self.assertEqual(install_code, 2)
            self.assertIn("INTERACTIVE_TTY_REQUIRED", errors.getvalue())
            after = {item.relative_to(self.base).as_posix() for item in self.base.rglob("*")}
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
