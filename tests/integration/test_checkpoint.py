from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import venv
from unittest import mock

from adapters.client import checkpoint as cp
from adapters.client import continuation as continuation
from adapters.client import task_finish as finish
from adapters.client.__main__ import main, _confirm_checkpoint
from adapters.client.project_locator import attach_project
from adapters.panel.application import open_panel_application


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        from tests.integration.test_continuation_commit import ContinuationCommitTests
        self.fixture = ContinuationCommitTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture._commit()
        finish.finish_daily_task(store=self.fixture.store, authority=self.fixture.authority,
            trace=self.fixture.fixture.trace,
            plan=self.fixture._finish_plan(self.fixture.current_start_plan, "finish-continuation"),
            confirmation=lambda *_: True)
        self.fixture.store.close()
        self.repo = self.fixture.repo_path
        self.base = self.fixture.base
        self.root = self.fixture.fixture.root
        self.registry = self.base / "host" / "projects-v1.json"
        self.binding = attach_project(project_root=self.repo, project_id="project-nexus",
            data_root=self.root, policy_path=self.fixture.fixture.policy_path,
            independent_purge_journal_path=self.fixture.fixture.journal,
            registry_path=self.registry, confirmation=lambda *_: True)
        self.binding["project_root"] = str(self.repo)
        self.fixture._git("add", ".nexus/project.json")
        self.fixture._git("commit", "-m", "fixture project identity")
        sha = self.fixture._git("rev-parse", "HEAD")
        self.fixture._git("update-ref", "refs/remotes/origin/main", sha)
        self.proposal = {"accepted_revision": sha, "current_objective": "Close the release review.",
            "next_step": "Independent review first.", "recent_work": "Completed an accepted bounded release."}
        self.nested = self.repo / "docs" / "user"
        self.nested.mkdir(parents=True)
        self.env = {"NEXUS_PROJECT_REGISTRY": str(self.registry)}

    def app(self, read_only=True):
        return open_panel_application(data_root=self.binding["data_root"], policy_path=self.binding["policy_path"],
            independent_purge_journal_path=self.binding["independent_purge_journal_path"], read_only=read_only)

    def envelope(self, checkpoint_id="release-one"):
        app = self.app()
        try:
            return cp.build_checkpoint(store=app.store, authority=cp.AuthorityService(app.store, app.store.policy),
                context_packs=app.context_packs, binding=self.binding, proposal=self.proposal,
                checkpoint_id=checkpoint_id)
        finally:
            app.close()

    def execute(self, envelope, confirmation=None):
        app = self.app(False)
        try:
            return cp.execute_checkpoint(envelope=envelope, git_repo=self.repo,
                confirmation=confirmation or (lambda *_: True), **cp.compose_services(app.store))
        finally:
            app.close()

    def canonical(self):
        app = self.app()
        try:
            with app.store._connection() as conn:
                return {name: [tuple(row) for row in conn.execute("SELECT * FROM " + name)] for name in (
                    "tasks", "runs", "delegation_grants", "objects", "classification_assertions", "logical_refs",
                    "object_relations", "context_pack_records", "command_ledger", "trace_events", "budget_accounts")}
        finally:
            app.close()

    def test_one_confirmation_happy_path_and_exact_historical_replay(self):
        envelope = self.envelope()
        self.assertEqual(envelope["continuation"]["git_fact"]["commit_sha"], self.proposal["accepted_revision"])
        self.assertEqual(envelope["continuation"]["git_fact"]["upstream_commit_sha"], self.proposal["accepted_revision"])
        calls = []
        result = self.execute(envelope, lambda phrase, summary: calls.append((phrase, summary)) or True)
        self.assertEqual(result["status"], "CHECKPOINT_COMPLETED")
        self.assertEqual(result["state_revision"], 3)
        self.assertEqual(result["task_outcome"], "SUCCEEDED")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "CHECKPOINT project-nexus")
        self.assertNotIn(str(self.root), json.dumps(calls))
        app = self.app()
        try:
            state = json.loads(app.store.get_payload(result["current_state_ref"]))
            self.assertEqual(state["current_objective"], self.proposal["current_objective"])
            self.assertEqual(app.context_packs.latest()["pack_id"], result["context_pack_ref"])
            self.assertEqual(app.context_packs.read_compiled(result["context_pack_ref"])["model_visible_exposure"], "UNKNOWN")
            projection = finish._load_projection(app.store, finish._validate_plan(app.store,
                cp.AuthorityService(app.store, app.store.policy), envelope["finish"]))
            self.assertEqual(projection["grant"]["status"], "REVOKED")
        finally:
            app.close()
        # A later legitimate Checkpoint must not break historical replay.
        newer = self.execute(self.envelope("release-two"))
        self.assertEqual(newer["status"], "CHECKPOINT_COMPLETED", newer)
        (self.repo / "later-release.txt").write_text("Later accepted release.\n", encoding="utf-8")
        self.fixture._git("add", "later-release.txt")
        self.fixture._git("commit", "-m", "fixture later accepted release")
        advanced = self.fixture._git("rev-parse", "HEAD")
        self.fixture._git("update-ref", "refs/remotes/origin/main", advanced)
        self.assertNotEqual(advanced, self.proposal["accepted_revision"])
        self.assertEqual(self.fixture._git("rev-parse", "@{u}"), advanced)
        before = self.canonical()
        with mock.patch.object(continuation, "_validate_git_fact", side_effect=AssertionError("live Git reread")):
            replay = self.execute(envelope, lambda *_: self.fail("confirmation on replay"))
        self.assertEqual(replay, {**result, "replayed": True})
        self.assertEqual(before, self.canonical())
        self.assertEqual(newer["state_revision"], 4)

    def test_local_head_not_upstream_fails_before_confirmation_without_writes(self):
        previous = self.fixture._git("rev-parse", "HEAD^")
        self.fixture._git("update-ref", "refs/remotes/origin/main", previous)
        self.assertEqual(self.fixture._git("rev-parse", "HEAD"), self.proposal["accepted_revision"])
        before = self.canonical()
        calls = []
        with mock.patch.dict(os.environ, self.env):
            with self.assertRaises(cp.CheckpointError) as error:
                cp.checkpoint_project(proposal=self.proposal, start_dir=self.nested,
                    confirmation=lambda *_: calls.append(True) or True)
        self.assertEqual(error.exception.reason_code, "CHECKPOINT_GIT_REVISION_NOT_UPSTREAM")
        self.assertEqual(calls, [])
        self.assertEqual(before, self.canonical())

    def test_receipt_git_fact_mismatch_fails_before_confirmation_without_writes(self):
        envelope = self.envelope("receipt-mismatch")
        envelope["continuation"]["git_fact"]["upstream_commit_sha"] = self.fixture._git("rev-parse", "HEAD^")
        _, prefix = cp._identity(self.binding, self.proposal, "receipt-mismatch")
        receipt = self.registry.parent / "checkpoints-v1" / self.binding["instance_id"] / (prefix + ".json")
        receipt.parent.mkdir(parents=True)
        receipt.write_bytes(cp._bytes(envelope))
        before = self.canonical()
        calls = []
        with mock.patch.dict(os.environ, self.env):
            with self.assertRaises(cp.CheckpointError) as error:
                cp.checkpoint_project(proposal=self.proposal, checkpoint_id="receipt-mismatch",
                    start_dir=self.nested, confirmation=lambda *_: calls.append(True) or True)
        self.assertEqual(error.exception.reason_code, "CHECKPOINT_GIT_REVISION_NOT_UPSTREAM")
        self.assertEqual(calls, [])
        self.assertEqual(before, self.canonical())

    def test_denial_zero_canonical_writes(self):
        envelope = self.envelope()
        before = self.canonical()
        with self.assertRaises(cp.CheckpointError) as error:
            self.execute(envelope, lambda *_: False)
        self.assertEqual(error.exception.reason_code, "CHECKPOINT_CONFIRMATION_DENIED")
        self.assertEqual(before, self.canonical())

    def test_lost_checkpoint_binding_response_recovers_durable_authorization(self):
        envelope = self.envelope()
        app = self.app(False)
        try:
            original = app.store.bind_command_request
            def interrupted(**kwargs):
                original(**kwargs)
                if kwargs["operation"] == cp.OPERATION:
                    raise RuntimeError("lost binding response")
            with mock.patch.object(app.store, "bind_command_request", side_effect=interrupted):
                result = cp.execute_checkpoint(envelope=envelope, git_repo=self.repo,
                    confirmation=lambda *_: True, **cp.compose_services(app.store))
            self.assertEqual(result["status"], "CHECKPOINT_PARTIAL_RESUMABLE")
        finally:
            app.close()
        self.assertEqual(self.execute(envelope, lambda *_: self.fail("binding already authorized"))["status"],
            "CHECKPOINT_COMPLETED")

    def test_changed_request_same_identity_conflicts_without_writes(self):
        envelope = self.envelope()
        self.execute(envelope)
        changed = copy.deepcopy(envelope)
        changed["proposal"]["next_step"] = "Changed request."
        changed["continuation"]["human_assertions"]["next_step"] = "Changed request."
        before = self.canonical()
        with self.assertRaises(cp.CheckpointError) as error:
            self.execute(changed, lambda *_: self.fail("changed request confirmation"))
        self.assertEqual(error.exception.reason_code, "COMMAND_CONFLICT")
        self.assertEqual(before, self.canonical())

    def test_crashes_after_request_start_continuation_and_during_finish_resume(self):
        for boundary in ("request", "start", "continuation", "finish-verifying", "finish-terminal"):
            with self.subTest(boundary=boundary):
                envelope = self.envelope("crash-" + boundary)
                calls = []
                original_start = cp.start.start_daily_task
                original_commit = cp.continuation.commit_continuation
                if boundary == "request":
                    target, replacement = (cp.start, "start_daily_task"), mock.Mock(side_effect=RuntimeError("crash"))
                elif boundary == "start":
                    def replacement(**kwargs):
                        original_start(**kwargs)
                        raise RuntimeError("lost Start response")
                    target = cp.start, "start_daily_task"
                elif boundary == "continuation":
                    def replacement(**kwargs):
                        original_commit(**kwargs)
                        raise RuntimeError("lost Continuation response")
                    target = cp.continuation, "commit_continuation"
                else:
                    original_transition = cp.TraceRuntime.transition_run
                    def replacement(trace, **kwargs):
                        value = original_transition(trace, **kwargs)
                        wanted = "VERIFYING" if boundary == "finish-verifying" else "SUCCEEDED"
                        if kwargs["command_id"].startswith(envelope["finish"]["command_id"]) and kwargs["next_state"] == wanted:
                            raise RuntimeError("lost Finish response")
                        return value
                    target = cp.TraceRuntime, "transition_run"
                with mock.patch.object(target[0], target[1], replacement):
                    partial = self.execute(envelope, lambda *_: calls.append(True) or True)
                self.assertEqual(partial["status"], "CHECKPOINT_PARTIAL_RESUMABLE")
                self.assertEqual(len(calls), 1)
                if boundary.startswith("finish"):
                    app = self.app()
                    try:
                        self.assertEqual(continuation._read_current_ref(app.store)["current_object_id"],
                            envelope["continuation"]["outputs"]["current_state_object_id"])
                    finally:
                        app.close()
                resumed = self.execute(envelope, lambda *_: self.fail("resume confirmation"))
                self.assertEqual(resumed["status"], "CHECKPOINT_COMPLETED", resumed)
                before = self.canonical()
                self.assertTrue(self.execute(envelope, lambda *_: self.fail("lost success response confirmation"))["replayed"])
                self.assertEqual(before, self.canonical())

    def test_partial_expired_authority_has_no_forward_mutation(self):
        envelope = self.envelope()
        with mock.patch.object(cp.continuation, "commit_continuation", side_effect=RuntimeError("interrupt")):
            self.assertEqual(self.execute(envelope)["status"], "CHECKPOINT_PARTIAL_RESUMABLE")
        future = cp.start._timestamp(envelope["start"]["grant"]["expires_at"])
        before = self.canonical()
        with mock.patch("kernel.authority.service._now", return_value=future):
            partial = self.execute(envelope, lambda *_: self.fail("expired confirmation"))
        self.assertEqual(partial["status"], "CHECKPOINT_PARTIAL_RESUMABLE")
        self.assertEqual(before, self.canonical())

    def test_partial_stale_request_cannot_overwrite_newer_state(self):
        stale = self.envelope("stale")
        with mock.patch.object(cp.start, "start_daily_task", side_effect=RuntimeError("interrupt")):
            self.execute(stale)
        self.execute(self.envelope("newer"))
        before = self.canonical()
        result = self.execute(stale, lambda *_: self.fail("stale confirmation"))
        self.assertEqual(result["status"], "CHECKPOINT_PARTIAL_RESUMABLE")
        self.assertIn(result["reason"], {"CONTINUATION_STATE_CONFLICT", "CONTINUATION_CONTEXT_CONFLICT"})
        self.assertEqual(before, self.canonical())

    def test_collision_preflight_and_short_unique_exact_resources(self):
        envelope = self.envelope()
        app = self.app()
        try:
            s, c, f, bound = cp._normalized(app.store, cp.AuthorityService(app.store, app.store.policy), envelope)
            ids = [*s["command_ids"], *c["command_ids"], *f["commands"], *s["grant"]["resource_scope"]]
            self.assertTrue(all(len(item) <= 128 and "*" not in item for item in ids))
            self.assertEqual(set(s["grant"]["action_scope"]),
                {"CLASSIFY", "INSPECT", "OBJECT_WRITE", "RUN_CREATE", "RUN_TRANSITION", "TRACE_APPEND"})
        finally:
            app.close()
        app = self.app(False)
        try:
            app.store.bind_command_request(command_id=envelope["start"]["command_id"] + ":request",
                operation="unrelated", request={"fixture": True})
        finally:
            app.close()
        before = self.canonical()
        with self.assertRaises(Exception):
            self.execute(envelope, lambda *_: self.fail("collision confirmation"))
        self.assertEqual(before, self.canonical())

    def test_host_receipt_nested_retry_no_rewrites_one_human_boundary(self):
        calls = []
        with mock.patch.dict(os.environ, self.env):
            result = cp.checkpoint_project(proposal=self.proposal, start_dir=self.nested,
                confirmation=lambda *args: calls.append(args) or True)
            receipt = next((self.registry.parent / "checkpoints-v1").rglob("cp-*.json"))
            frozen = receipt.read_bytes(), receipt.stat().st_mtime_ns
            def files():
                paths = [*self.root.rglob("*"), *self.registry.parent.rglob("*"),
                         self.repo / ".nexus" / "project.json", self.fixture.fixture.journal]
                return {str(path): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size,
                    path.stat().st_mtime_ns) for path in paths if path.is_file()}
            frozen_files = files()
            before = self.canonical()
            replay = cp.checkpoint_project(proposal=self.proposal, start_dir=self.repo,
                confirmation=lambda *_: self.fail("host exact replay prompt"))
            self.assertEqual(frozen_files, files())
        self.assertEqual(result["status"], "CHECKPOINT_COMPLETED")
        self.assertEqual(len(calls), 1)
        self.assertTrue(replay["replayed"])
        self.assertEqual(frozen, (receipt.read_bytes(), receipt.stat().st_mtime_ns))
        self.assertEqual(before, self.canonical())

    def test_cli_non_tty_never_opens_writer_or_changes_canonical_state(self):
        before = self.canonical()
        proposal = self.base / "proposal.json"
        proposal.write_text(json.dumps(self.proposal), encoding="utf-8")
        output, error = io.StringIO(), io.StringIO()
        original = cp.open_panel_application
        def opener(**kwargs):
            self.assertTrue(kwargs["read_only"])
            return original(**kwargs)
        with mock.patch.dict(os.environ, self.env), mock.patch("pathlib.Path.cwd", return_value=self.nested), \
            mock.patch.object(cp, "open_panel_application", side_effect=opener), \
            contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = main(["checkpoint", "--proposal", str(proposal)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(error.getvalue())["reason"], "INTERACTIVE_TTY_REQUIRED")
        self.assertEqual(before, self.canonical())

    def test_confirmation_ui_contains_whole_semantics_no_internal_id_noise(self):
        envelope = self.envelope()
        class TTY(io.StringIO):
            def isatty(self):
                return True
        output = TTY()
        with mock.patch("sys.stdin", TTY("CHECKPOINT project-nexus\n")), mock.patch("sys.stdout", output):
            self.assertTrue(_confirm_checkpoint("CHECKPOINT project-nexus", cp.checkpoint_summary(envelope)))
        for value in self.proposal.values():
            self.assertIn(value, output.getvalue())
        self.assertNotIn(envelope["start"]["grant"]["grant_id"], output.getvalue())
        self.assertNotIn(envelope["start"]["root"]["root_run_id"], output.getvalue())
        self.assertNotIn(envelope["start"]["root"]["classifications"]["root_run"]["assertion_id"], output.getvalue())

    def test_denied_host_confirmation_zero_file_or_canonical_writes(self):
        paths = [*self.root.rglob("*"), self.fixture.fixture.journal, self.registry,
                 self.repo / ".nexus" / "project.json"]
        def commitment():
            return {str(path): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size,
                path.stat().st_mtime_ns) for path in paths if path.is_file()}
        before = commitment()
        with mock.patch.dict(os.environ, self.env):
            with self.assertRaises(cp.CheckpointError) as caught:
                cp.checkpoint_project(proposal=self.proposal, start_dir=self.nested, confirmation=lambda *_: False)
        self.assertEqual(caught.exception.reason_code, "CHECKPOINT_CONFIRMATION_DENIED")
        self.assertEqual(before, commitment())
        self.assertEqual(list((self.registry.parent / "checkpoints-v1").rglob("*.json")), [])

    def test_host_receipt_without_canonical_binding_never_proves_authorization(self):
        calls = []
        with mock.patch.dict(os.environ, self.env):
            with mock.patch.object(cp, "execute_checkpoint", side_effect=RuntimeError("crash before binding")):
                with self.assertRaises(RuntimeError):
                    cp.checkpoint_project(proposal=self.proposal, start_dir=self.repo,
                        confirmation=lambda *_: calls.append(True) or True)
            self.assertEqual(len(list((self.registry.parent / "checkpoints-v1").rglob("*.json"))), 1)
            result = cp.checkpoint_project(proposal=self.proposal, start_dir=self.nested,
                confirmation=lambda *_: calls.append(True) or True)
        self.assertEqual(result["status"], "CHECKPOINT_COMPLETED")
        self.assertEqual(len(calls), 2)

    def test_cli_semantic_flags_one_tty_confirmation_and_non_tty_exact_replay(self):
        class TTY(io.StringIO):
            def isatty(self):
                return True
        arguments = ["checkpoint", "--accepted-revision", self.proposal["accepted_revision"],
            "--objective", self.proposal["current_objective"], "--next", self.proposal["next_step"],
            "--completed", self.proposal["recent_work"]]
        output, error = TTY(), io.StringIO()
        with mock.patch.dict(os.environ, self.env), mock.patch("pathlib.Path.cwd", return_value=self.nested), \
            mock.patch("sys.stdin", TTY("CHECKPOINT project-nexus\n")), \
            contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            self.assertEqual(main(arguments), 0, error.getvalue())
        self.assertEqual(output.getvalue().count("输入 CHECKPOINT project-nexus 确认"), 1)
        self.assertIn('"CHECKPOINT_COMPLETED"', output.getvalue())
        before = self.canonical()
        output = io.StringIO()
        with mock.patch.dict(os.environ, self.env), mock.patch("pathlib.Path.cwd", return_value=self.repo), \
            contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            self.assertEqual(main(arguments), 0, error.getvalue())
        self.assertTrue(json.loads(output.getvalue())["replayed"])
        self.assertEqual(before, self.canonical())

    def test_continuation_partial_snapshot_and_cas_recover_without_confirmation(self):
        original_commit = cp.continuation.commit_continuation
        for stage in ("request_bound", "snapshot:00", "current_ref_updated"):
            with self.subTest(stage=stage):
                envelope = self.envelope("partial-" + stage.replace(":", "-"))
                def interrupted(**kwargs):
                    def stop(current):
                        if current == stage:
                            raise RuntimeError("interrupted Continuation")
                    return original_commit(**kwargs, _stage_hook=stop)
                with mock.patch.object(cp.continuation, "commit_continuation", side_effect=interrupted):
                    self.assertEqual(self.execute(envelope)["status"], "CHECKPOINT_PARTIAL_RESUMABLE")
                self.assertEqual(self.execute(envelope, lambda *_: self.fail("partial confirmation"))["status"],
                    "CHECKPOINT_COMPLETED")

    def test_scope_tampering_and_dirty_git_fail_before_confirmation(self):
        envelope = self.envelope()
        changed = copy.deepcopy(envelope)
        changed["start"]["grant"]["action_scope"].append("VERIFY")
        with self.assertRaises(cp.CheckpointError):
            self.execute(changed, lambda *_: self.fail("altered scope prompt"))
        (self.repo / "untracked-file.txt").write_text("dirty fixture", encoding="utf-8")
        before = self.canonical()
        with self.assertRaises(continuation.ContinuationCommitError):
            self.execute(envelope, lambda *_: self.fail("dirty Git prompt"))
        self.assertEqual(before, self.canonical())

    def test_installed_nexus_from_ordinary_powershell_root_and_nested_without_pythonpath(self):
        repository = Path(__file__).resolve().parents[2]
        directory = self.base / "launcher-venv"
        # Reuse installed build dependencies offline; ensurepip's bundled old
        # setuptools would shadow the reviewed host build environment.
        venv.EnvBuilder(with_pip=False, system_site_packages=True).create(directory)
        scripts = directory / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        install = subprocess.run([str(python), "-m", "pip", "install", "--no-deps", "--no-build-isolation",
            "--editable", str(repository)], cwd=self.base, capture_output=True, check=False)
        self.assertEqual(install.returncode, 0, install.stderr.decode("utf-8", "replace"))
        env = {**os.environ, **self.env}
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        env["PATH"] = str(scripts) + os.pathsep + env.get("PATH", "")
        if os.name == "nt":
            command = ["powershell.exe", "-NoProfile", "-Command", "nexus checkpoint --help"]
        else:
            command = [str(scripts / "nexus"), "checkpoint", "--help"]
        for cwd in (self.repo, self.nested):
            result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, stdin=subprocess.DEVNULL)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
            self.assertIn(b"--accepted-revision", result.stdout)
        proposal = self.base / "launcher-proposal.json"
        proposal.write_text(json.dumps(self.proposal), encoding="utf-8")
        before = self.canonical()
        for cwd in (self.repo, self.nested):
            if os.name == "nt":
                command = ["powershell.exe", "-NoProfile", "-Command",
                    "nexus checkpoint --proposal '" + str(proposal).replace("'", "''") + "'"]
            else:
                command = [str(scripts / "nexus"), "checkpoint", "--proposal", str(proposal)]
            result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, stdin=subprocess.DEVNULL)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"INTERACTIVE_TTY_REQUIRED", result.stderr)
            self.assertNotIn(b"Traceback", result.stderr)
        self.assertEqual(before, self.canonical())


if __name__ == "__main__":
    unittest.main()
