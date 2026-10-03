from __future__ import annotations

import contextlib
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import venv
from unittest import mock

from adapters.client.__main__ import _emit_json_document, main
from adapters.client.presence import (
    _WORKSPACE_TOKEN_LIMIT,
    _workspace_token_estimate,
    _last_completed,
    build_project_workspace,
    doctor_project,
    render_continue,
    _fit_workspace_budget,
)
from adapters.client.codex_host import (
    SESSION_OUTPUT_TOKEN_LIMIT,
    install_codex_host,
    session_start_output,
    session_text_token_estimate,
)
from adapters.client.project_locator import attach_project
from tests.support.test_store import open_test_store


def _tree_commitment(*roots: Path) -> dict[str, str]:
    result = {}
    for index, root in enumerate(roots):
        if not root.exists():
            result[f"{index}:MISSING"] = "MISSING"
            continue
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root).as_posix()
            if path.is_file():
                result[f"{index}:{relative}"] = hashlib.sha256(path.read_bytes()).hexdigest()
            elif path.is_dir():
                result[f"{index}:{relative}/"] = "DIR"
    return result


class NativePresenceTests(unittest.TestCase):
    def setUp(self):
        from tests.integration.test_continuation_commit import ContinuationCommitTests

        self.continuation = ContinuationCommitTests()
        self.continuation.setUp()
        self.addCleanup(self.continuation.doCleanups)
        self.root = self.continuation.store.data_root
        self.journal = self.continuation.fixture.journal
        self.policy = self.continuation.fixture.policy_path
        self.base = self.continuation.base
        self.repo = self.base / "project-repo"
        self.repo.mkdir()
        self.nested = self.repo / "docs" / "user"
        self.nested.mkdir(parents=True)
        self.registry = self.base / "host" / "projects-v1.json"
        result = self.continuation._commit(confirmation=lambda *_: True)
        self.assertEqual(result["status"], "CONTINUATION_COMMITTED")
        self.commit_result = result
        self.continuation.store.close()
        self.attachment = attach_project(
            project_root=self.repo, project_id="project-test-native",
            data_root=self.root, policy_path=self.policy,
            independent_purge_journal_path=self.journal,
            registry_path=self.registry, confirmation=lambda *_: True,
        )
        self.host_config = self.base / "codex-home" / "hooks.json"
        self.env = {
            "NEXUS_PROJECT_REGISTRY": str(self.registry),
            "CODEX_HOME": str(self.host_config.parent),
        }

    def _run_cli(self, args, *, cwd=None):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, self.env, clear=False), \
             mock.patch("pathlib.Path.cwd", return_value=cwd or self.repo), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_status_from_root_and_nested_directory(self):
        root = self._run_cli(["status"], cwd=self.repo)
        nested = self._run_cli(["status"], cwd=self.nested)
        self.assertEqual(root[0], 0, root[2])
        self.assertEqual(nested[0], 0, nested[2])
        self.assertIn("Nexus 状态", root[1])
        self.assertIn("已连接", nested[1])
        self.assertIn("已验证", nested[1])
        self.assertNotIn(str(self.root), root[1])

    def test_status_json_has_stable_english_keys_and_read_only_projection(self):
        before = _tree_commitment(self.root, self.journal.parent)
        code, output, error = self._run_cli(["status", "--json"], cwd=self.nested)
        after = _tree_commitment(self.root, self.journal.parent)
        self.assertEqual(code, 0, error)
        result = json.loads(output)
        self.assertEqual(result["status"], "PROJECT_WORKSPACE")
        self.assertEqual(result["project"]["project_id"], "project-test-native")
        self.assertEqual(result["instance"]["verification"], "VERIFIED")
        self.assertEqual(result["runtime"]["mode"], "NORMAL")
        self.assertEqual(result["current_state"]["accepted_revision"], self.continuation.git_fact["commit_sha"])
        self.assertEqual(result["context"]["model_visible_exposure"], "UNKNOWN")
        self.assertEqual(before, after)
        self.assertNotIn(str(self.root), output)

    def test_continue_from_root_and_nested_directory_is_compact(self):
        root = self._run_cli(["continue"], cwd=self.repo)
        nested = self._run_cli(["continue", "--json"], cwd=self.nested)
        self.assertEqual(root[0], 0, root[2])
        self.assertEqual(nested[0], 0, nested[2])
        self.assertIn("当前目标:", root[1])
        self.assertIn(
            f"已 supersede: {self.commit_result['previous_current_state_ref']}",
            root[1],
        )
        self.assertIn(
            f"最近变化: accepted revision → {self.continuation.git_fact['commit_sha']}",
            root[1],
        )
        result = json.loads(nested[1])
        self.assertEqual(result["current_state"]["status"], "AVAILABLE")
        self.assertEqual(result["what_changed"]["status"], "AVAILABLE")
        self.assertEqual(result["what_changed"]["previous_state"]["object_id"],
                         "prior-current-state")
        self.assertEqual(result["what_changed"]["new_state"]["object_id"],
                         self.commit_result["new_current_state_ref"])
        self.assertEqual(result["what_changed"]["accepted_revision"]["after_ref"],
                         "current_state.accepted_revision")
        self.assertIsNone(result["current_state"]["critical_constraints"])
        self.assertEqual(result["context"]["model_visible_exposure"], "UNKNOWN")
        self.assertIn("current_state", result["references"])
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertLess(len(serialized), 12000)
        self.assertLessEqual(_workspace_token_estimate(result), _WORKSPACE_TOKEN_LIMIT)
        self.assertNotIn("Stable accepted product boundary.", serialized)
        self.assertNotIn(str(self.root), serialized)

    def test_workspace_builder_excludes_full_54kb_context_entry(self):
        body = "PRIVATE-CONTEXT-MUST-NOT-LEAK-" + ("x" * 54000)

        class FakeViewModel:
            def snapshot(self):
                return {
                    "runtime_mode": "NORMAL", "participation_mode": "ACTIVE",
                    "overview": {"task_count": 0, "recorded_run_count": 0,
                                  "unfinished_task_count": 0, "active_run_count": 0,
                                  "pending_effect_count": 0},
                    "tasks": [], "skill_status": {"status": "OBSERVED"},
                }

        class FakeContext:
            def latest(self):
                return {"status": "PACK_COMPILED", "pack_id": "pack-large",
                        "content_hash": "a" * 64, "integrity_hash": "b" * 64,
                        "serialized_byte_size": 54000, "compiled_at": "now"}

            def read_compiled(self, _ref):
                return {"status": "CONTEXT_PACK_READ", "model_visible_exposure": "UNKNOWN",
                        "entries": [{"source_ref": "artifact-large", "content": body,
                                     "source_integrity_sha256": "c" * 64}]}

        class FakeApp:
            view_model = FakeViewModel()
            context_packs = FakeContext()

            def close(self):
                pass

        binding = {
            "project_id": "project-test", "instance_id": "instance-test",
            "policy_version": "1", "policy_sha256": "a" * 64,
            "journal_identity": "b" * 64, "data_root": "unused",
            "policy_path": None, "independent_purge_journal_path": "unused",
        }
        workspace = build_project_workspace(
            start_dir=self.repo,
            locator=lambda **_: binding,
            app_opener=lambda *_args, **_kwargs: FakeApp(),
        )
        serialized = json.dumps(workspace, ensure_ascii=False)
        self.assertIn("artifact-large", serialized)
        self.assertNotIn("PRIVATE-CONTEXT-MUST-NOT-LEAK", serialized)
        self.assertLess(len(serialized), 5000)
        self.assertLessEqual(_workspace_token_estimate(workspace), _WORKSPACE_TOKEN_LIMIT)
        self.assertEqual(workspace["context"]["model_visible_exposure"], "UNKNOWN")

    def test_workspace_budget_accepts_full_continuation_projection(self):
        code, output, error = self._run_cli(["continue", "--json"], cwd=self.nested)
        self.assertEqual(code, 0, error)
        workspace = json.loads(output)
        current_next = "Continue using the verified Project Nexus state; " * 10
        workspace["current_state"]["next_step"] = current_next
        workspace["what_changed"]["objective"]["before"] = "Previous accepted objective " * 8
        workspace["what_changed"]["next_step"]["before"] = "Previous continuation step " * 8
        workspace["what_changed"]["facts_superseded"] = [
            {"fact": f"superseded-{index}", "reason": "replaced by accepted continuation"}
            for index in range(8)
        ]
        workspace["what_changed"]["recent_work_added"]["summary"] = (
            "Accepted governed continuation and verified the fresh Context projection. " * 5
        )
        expected_objective = workspace["current_state"]["objective"]

        fitted = _fit_workspace_budget(workspace)

        self.assertLessEqual(_workspace_token_estimate(fitted), _WORKSPACE_TOKEN_LIMIT)
        self.assertEqual(fitted["current_state"]["objective"], expected_objective)
        self.assertEqual(fitted["current_state"]["next_step"], current_next)
        self.assertNotIn("next", fitted)
        self.assertEqual(fitted["context"]["model_visible_exposure"], "UNKNOWN")

    def test_budget_trim_shortens_long_superseded_next_step_without_looping(self):
        code, output, error = self._run_cli(["continue", "--json"], cwd=self.repo)
        self.assertEqual(code, 0, error)
        workspace = deepcopy(json.loads(output))
        previous_next = "旧的 continuation priority。" * 180
        workspace["what_changed"]["next_step"]["before"] = previous_next
        workspace["what_changed"]["objective"]["before"] = "previous objective"
        original_summary = workspace["last_completed"]["summary"]
        original_objective = workspace["current_state"]["objective"]
        original_next = workspace["current_state"]["next_step"]
        fitted = _fit_workspace_budget(workspace)
        self.assertLessEqual(_workspace_token_estimate(fitted), _WORKSPACE_TOKEN_LIMIT)
        self.assertLess(len(fitted["what_changed"]["next_step"]["before"]), len(previous_next))
        self.assertEqual(fitted["what_changed"]["next_step"]["after_ref"], "current_state.next_step")
        self.assertEqual(fitted["current_state"]["objective"], original_objective)
        self.assertEqual(fitted["current_state"]["next_step"], original_next)
        self.assertEqual(fitted["last_completed"]["summary"], original_summary)
        self.assertEqual(fitted["what_changed"]["recent_work_added"]["summary_ref"],
                         "last_completed.summary")

    def test_ambiguous_panel_history_does_not_invent_last_completed(self):
        self.assertIsNone(_last_completed(None, [
            {"task_id": "task-old", "status": "SUCCEEDED", "root_run_id": "run-old", "runs": []},
            {"task_id": "task-new", "status": "SUCCEEDED", "root_run_id": "run-new", "runs": []},
        ]))

    def test_doctor_healthy_binding_hook_and_hook_trust_unknown(self):
        with mock.patch("adapters.client.codex_host.shutil.which", return_value="nexus.exe"):
            installed = install_codex_host(
                config_path=self.host_config,
                confirmation=lambda expected, _summary: expected == "INSTALL NEXUS HOST ADAPTER CODEX",
            )
        self.assertEqual(installed["status"], "HOST_ADAPTER_INSTALLED")
        with mock.patch.dict(os.environ, self.env, clear=False):
            report = doctor_project(start_dir=self.nested)
        statuses = {item["name"]: item["status"] for item in report["checks"]}
        self.assertEqual(statuses["project_manifest"], "PASS")
        self.assertEqual(statuses["instance_verification"], "PASS")
        self.assertEqual(statuses["latest_context"], "PASS")
        self.assertEqual(statuses["session_start_hook"], "PASS")
        self.assertEqual(statuses["hook_trust"], "UNKNOWN")

    def test_doctor_missing_registry_explains_binding_gap(self):
        self.registry.unlink()
        with mock.patch.dict(os.environ, self.env, clear=False):
            report = doctor_project(start_dir=self.repo)
        checks = {item["name"]: item for item in report["checks"]}
        self.assertEqual(checks["host_registry"]["status"], "PASS")
        self.assertEqual(checks["project_binding"]["status"], "FAIL")
        self.assertIn("attach", checks["project_binding"]["remediation"])

    def test_doctor_broken_managed_policy_is_reported_without_repair(self):
        document = json.loads(self.registry.read_text(encoding="utf-8"))
        policy_path = Path(document["projects"]["project-test-native"]["policy_path"])
        policy_path.write_text("{}", encoding="utf-8")
        before_registry = self.registry.read_bytes()
        with mock.patch.dict(os.environ, self.env, clear=False):
            report = doctor_project(start_dir=self.repo)
        checks = {item["name"]: item for item in report["checks"]}
        self.assertEqual(checks["managed_policy"]["status"], "FAIL")
        self.assertEqual(self.registry.read_bytes(), before_registry)

    def test_doctor_reports_missing_current_state_and_context(self):
        empty_base = self.base / "empty"
        empty_base.mkdir()
        data_root = empty_base / "instance"
        journal = empty_base / "journal.jsonl"
        store = open_test_store(data_root, independent_purge_journal_path=journal)
        store.close()
        repo = empty_base / "repo"
        repo.mkdir()
        registry = empty_base / "host" / "projects.json"
        policy = data_root.parent / f".{data_root.name}.test-policy.json"
        attach_project(project_root=repo, project_id="project-empty", data_root=data_root,
                       policy_path=policy, independent_purge_journal_path=journal,
                       registry_path=registry, confirmation=lambda *_: True)
        with mock.patch.dict(os.environ, {
            "NEXUS_PROJECT_REGISTRY": str(registry),
            "CODEX_HOME": str(self.base / "empty-codex-home"),
        }, clear=False):
            report = doctor_project(start_dir=repo)
        checks = {item["name"]: item["status"] for item in report["checks"]}
        self.assertEqual(checks["current_state"], "WARN")
        self.assertEqual(checks["latest_context"], "WARN")
        self.assertEqual(checks["latest_what_changed"], "WARN")

    def test_session_start_lifecycle_sources_nested_directory_and_bounded_output(self):
        before = _tree_commitment(self.root, self.journal.parent)
        with mock.patch.dict(os.environ, self.env, clear=False):
            outputs = [session_start_output({"cwd": str(self.nested), "source": source})
                       for source in ("startup", "resume", "clear", "compact")]
        after = _tree_commitment(self.root, self.journal.parent)
        self.assertTrue(all(output for output in outputs))
        self.assertTrue(all("Nexus Project Workspace" in output for output in outputs))
        self.assertTrue(all(session_text_token_estimate(output) <= SESSION_OUTPUT_TOKEN_LIMIT
                            for output in outputs))
        self.assertTrue(all("UNKNOWN" in output for output in outputs))
        self.assertEqual(before, after)

    def test_session_start_expected_failure_is_fail_soft(self):
        self.assertEqual(session_start_output({"cwd": str(self.base / "unattached")}), "")
        self.assertEqual(session_start_output({"cwd": ""}), "")

    def test_explicit_data_root_context_read_and_module_launcher_remain_supported(self):
        code, output, error = self._run_cli([
            "--data-root", str(self.root), "--policy", str(self.policy),
            "--independent-purge-journal", str(self.journal), "context", "read", "--latest",
        ], cwd=self.nested)
        self.assertEqual(code, 0, error)
        self.assertEqual(json.loads(output)["status"], "CONTEXT_PACK_READ")
        result = subprocess.run(
            [sys.executable, "-m", "adapters.client", "--help"], cwd=Path(__file__).resolve().parents[2],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: nexus", result.stdout)

    def test_redirected_machine_json_is_utf8(self):
        repository = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            [sys.executable, "-m", "adapters.client", "status", "--json"],
            cwd=self.repo, env={**os.environ, "PYTHONPATH": str(repository), **self.env},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        document = json.loads(result.stdout.decode("utf-8"))
        self.assertEqual(document["project"]["project_id"], "project-test-native")
        self.assertEqual(document["context"]["model_visible_exposure"], "UNKNOWN")

        context_result = subprocess.run(
            [sys.executable, "-m", "adapters.client", "context", "read", "--latest"],
            cwd=self.nested, env={**os.environ, "PYTHONPATH": str(repository), **self.env},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False, check=False,
        )
        self.assertEqual(context_result.returncode, 0,
                         context_result.stderr.decode("utf-8", "replace"))
        context = json.loads(context_result.stdout.decode("utf-8"))
        self.assertEqual(context["status"], "CONTEXT_PACK_READ")
        self.assertEqual(context["pack_id"], self.commit_result["context_pack_ref"])

    def test_installed_nexus_launcher_works_nested_without_pythonpath(self):
        repository = Path(__file__).resolve().parents[2]
        environment_dir = self.base / "venv"
        venv.EnvBuilder(with_pip=True, system_site_packages=True).create(environment_dir)
        python = environment_dir / "Scripts" / "python.exe" if os.name == "nt" else environment_dir / "bin" / "python"
        script = environment_dir / "Scripts" / "nexus.exe" if os.name == "nt" else environment_dir / "bin" / "nexus"
        install = subprocess.run(
            [str(python), "-m", "pip", "install", "--editable", str(repository),
             "--no-deps"],
            cwd=self.nested, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
        )
        self.assertEqual(install.returncode, 0, install.stderr)
        child_env = dict(os.environ)
        child_env.pop("PYTHONPATH", None)
        child_env["NEXUS_PROJECT_REGISTRY"] = str(self.registry)
        result = subprocess.run(
            [str(script), "status", "--json"], cwd=self.nested, env=child_env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        self.assertEqual(json.loads(result.stdout.decode("utf-8"))["project"]["project_id"], "project-test-native")
        hook_input = json.dumps({"cwd": str(self.nested), "source": "startup"})
        before = _tree_commitment(self.root, self.journal.parent)
        hook_result = subprocess.run(
            [str(script), "hook", "session-start"], cwd=self.nested, env=child_env,
            input=hook_input, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
        )
        self.assertEqual(hook_result.returncode, 0, hook_result.stderr)
        self.assertIn("Nexus Project Workspace", hook_result.stdout)
        self.assertIn(self.continuation.git_fact["commit_sha"], hook_result.stdout)
        self.assertLessEqual(session_text_token_estimate(hook_result.stdout), SESSION_OUTPUT_TOKEN_LIMIT)
        if os.name == "nt":
            windows_env = dict(child_env)
            windows_env["PATH"] = str(script.parent) + os.pathsep + windows_env.get("PATH", "")
            windows_hook = subprocess.run(
                ["cmd.exe", "/d", "/c", "nexus hook session-start"], cwd=self.nested,
                env=windows_env, input=hook_input, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, check=False,
            )
            self.assertEqual(windows_hook.returncode, 0, windows_hook.stderr)
            self.assertIn(self.continuation.git_fact["commit_sha"], windows_hook.stdout)
        self.assertEqual(before, _tree_commitment(self.root, self.journal.parent))


class NativePresenceFailureTests(unittest.TestCase):
    def test_status_and_continue_fail_clearly_outside_attached_project(self):
        with tempfile.TemporaryDirectory(prefix="nexus-presence-unattached-") as temp:
            output = io.StringIO()
            errors = io.StringIO()
            with mock.patch.dict(os.environ, {"NEXUS_PROJECT_REGISTRY": str(Path(temp) / "registry.json")}), \
                 mock.patch("pathlib.Path.cwd", return_value=Path(temp)), \
                 contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                code = main(["status"])
            self.assertEqual(code, 2)
            self.assertIn("PROJECT_NOT_ATTACHED", errors.getvalue())
            output = io.StringIO()
            errors = io.StringIO()
            with mock.patch.dict(os.environ, {"NEXUS_PROJECT_REGISTRY": str(Path(temp) / "registry.json")}), \
                 mock.patch("pathlib.Path.cwd", return_value=Path(temp)), \
                 contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                code = main(["continue", "--json"])
            self.assertEqual(code, 2)
            self.assertIn("PROJECT_NOT_ATTACHED", errors.getvalue())
