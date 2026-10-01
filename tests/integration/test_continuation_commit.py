from __future__ import annotations

import copy
import io
import hashlib
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from adapters.client import continuation as continuation_module
from adapters.client.continuation import (
    ContinuationCommitError,
    PROTOCOL_VERSION,
    commit_continuation,
    prepare_continuation_commit,
    read_continuation_plan,
)
from adapters.client.__main__ import _confirm_continuation, main as client_main
from adapters.client.task_finish import finish_daily_task
from adapters.client.task_start import start_daily_task
from kernel.context import ContextPackService
from kernel.metering import MeteringService
from kernel.memory.service import MemoryService
from kernel.participation import ParticipationModeService


class ContinuationCommitTests(unittest.TestCase):
    """Disposable integration coverage over the existing Core write APIs."""

    def setUp(self):
        from tests.integration.test_daily_task_start import DailyTaskStartTests

        self.fixture = DailyTaskStartTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.store = self.fixture.store
        self.authority = self.fixture.authority
        self.base = self.fixture.base
        self.context = ContextPackService(
            store=self.store, authority=self.authority,
            participation=ParticipationModeService(self.store),
            memory=MemoryService(self.store, self.authority, verifier=None),
            metering=MeteringService(self.store, self.authority, ParticipationModeService(self.store)),
        )
        self.old_revision = "8576e996366b9acf9e0e55b0e3580e1fa04d5f22"
        self.repo_path, self.git_fact = self._make_git_repo()
        self.previous_plan, self.previous_pack = self._seed_previous_context()
        self.plan = self._start_current_task()

    @staticmethod
    def _now(delta: timedelta = timedelta()) -> str:
        return (datetime.now(timezone.utc) + delta).isoformat(timespec="microseconds").replace("+00:00", "Z")

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.repo_path), *args], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        )
        return result.stdout.strip()

    def _make_git_repo(self):
        path = self.base / "accepted-repo"
        path.mkdir()
        subprocess.run(["git", "init", "-b", "main", str(path)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.repo_path = path
        self._git("config", "user.name", "Disposable Test")
        self._git("config", "user.email", "disposable@example.invalid")
        (path / "README.md").write_text("fixture\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-m", "fixture")
        commit = self._git("rev-parse", "HEAD")
        self._git("update-ref", "refs/remotes/origin/main", commit)
        self._git("config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
        self._git("config", "branch.main.remote", "origin")
        self._git("config", "branch.main.merge", "refs/heads/main")
        return path, {
            "repository_id": "project-nexus-repo", "branch": "main", "commit_sha": commit,
            "worktree_state": "CLEAN", "upstream_ref": "origin/main", "upstream_commit_sha": commit,
        }

    def _extra_resources(self, plan, extra):
        plan["grant"]["additional_resource_scope"] = sorted(
            set(plan["grant"]["additional_resource_scope"]) | set(extra), key=lambda item: item.encode("utf-8"),
        )

    def _seed_previous_context(self):
        plan = self.fixture.plan("prior")
        task_id = plan["root"]["task_id"]
        run_id = plan["root"]["root_run_id"]
        state_id, source_id, pack_id = "prior-current-state", "stable-product-fact", "prior-context-pack"
        finish_command = "finish-prior"
        finish_refs = {f"evt-{finish_command}:verifying", f"evt-{finish_command}:terminal"}
        extra = {
            state_id, source_id, pack_id, "object:" + state_id, "object:" + source_id,
            *finish_refs,
        }
        self._extra_resources(plan, extra)
        start_daily_task(
            store=self.store, authority=self.authority, budget=self.fixture.budget,
            trace=self.fixture.trace, runtime=self.fixture.runtime, verifier=self.fixture.verifier,
            plan=plan, confirmation=lambda *_: True,
        )

        def classification(assertion_id, subject_ref):
            return {
                "schema_id": "nexus.classification_assertion", "schema_version": 1,
                "assertion_id": assertion_id, "subject_type": "OBJECT", "subject_ref": subject_ref,
                "sensitivity_level": "PUBLIC", "handling_tags": [], "policy_version": "1",
                "reason": "Disposable continuation fixture.", "actor_id": "runtime-service",
            }

        state_doc = {
            "schema_id": "nexus.continuation_state", "schema_version": 1,
            "project_identity": "project-nexus", "accepted_revision": self.old_revision,
            "current_objective": "Finish the bootstrap acceptance work.",
            "current_operating_priority": "Start first daily dogfood.",
            "as_of": self._now(timedelta(seconds=-5)),
            "source_refs": [source_id], "retained_fixture_fact": "Preserve this historical fact.",
        }
        state_payload = json.dumps(state_doc, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"), allow_nan=False).encode("utf-8")
        for assertion, object_id, payload in (
            (classification("class-prior-state", state_id), state_id, state_payload),
            (classification("class-stable-fact", source_id), source_id, b"Stable accepted product boundary.\n"),
        ):
            self.authority.record_classification_assertion(
                assertion, grant_id=plan["grant"]["grant_id"], task_id=task_id,
                audience="nexus-runtime", command_id="record-" + assertion["assertion_id"],
            )
            self.store.put_object(
                command_id="put-" + object_id, object_id=object_id, payload=payload,
                object_type="artifact", created_by_run=run_id,
                classification_assertion_ref=assertion["assertion_id"],
            )

        pack_class = {
            "schema_id": "nexus.classification_assertion", "schema_version": 1,
            "assertion_id": "class-prior-pack", "subject_type": "OBJECT", "subject_ref": pack_id,
            "sensitivity_level": "PUBLIC", "handling_tags": [], "policy_version": "1",
            "reason": "Disposable continuation fixture Context Pack.", "actor_id": "runtime-service",
        }
        self.authority.record_classification_assertion(
            pack_class, grant_id=plan["grant"]["grant_id"], task_id=task_id,
            audience="nexus-runtime", command_id="record-prior-pack-class",
        )
        context_result = self.context.compile(
            task_id=task_id, run_id=run_id, grant_id=plan["grant"]["grant_id"],
            pack_object_id=pack_id, classification_assertion_ref="class-prior-pack",
            command_id="compile-prior-context", source_refs=[state_id, source_id], memory_query=None,
        )
        finish_plan = self._finish_plan(plan, finish_command)
        finish_daily_task(
            store=self.store, authority=self.authority, trace=self.fixture.trace,
            plan=finish_plan, confirmation=lambda *_: True,
        )
        self.previous = {
            "state_id": state_id, "state_hash": hashlib.sha256(state_payload).hexdigest(),
            "pack_id": pack_id, "content_hash": context_result["content_hash"],
            "integrity_hash": context_result["integrity_hash"], "source_id": source_id,
        }
        return plan, context_result

    def _finish_plan(self, start_plan, command_id):
        task_id = start_plan["root"]["task_id"]
        run_id = start_plan["root"]["root_run_id"]
        def assertion(suffix, event):
            return {
                "schema_id": "nexus.classification_assertion", "schema_version": 1,
                "assertion_id": f"finish-class-{task_id}-{suffix}", "subject_type": "TRACE_EVENT",
                "subject_ref": event, "sensitivity_level": "PUBLIC", "handling_tags": [],
                "policy_version": "1", "reason": "Disposable Task finish.", "actor_id": "runtime-service",
            }
        return {
            "protocol_version": "nexus.daily_task_finish@1", "command_id": command_id,
            "instance_expectation": copy.deepcopy(start_plan["instance_expectation"]),
            "operator_principal_id": "operator-human", "task_id": task_id,
            "root_run_id": run_id, "grant_id": start_plan["grant"]["grant_id"], "outcome": "SUCCEEDED",
            "classifications": {
                "verifying_event": assertion("verifying", f"evt-{command_id}:verifying"),
                "terminal_event": assertion("terminal", f"evt-{command_id}:terminal"),
            },
        }

    def _start_current_task(self):
        label = "continuation"
        plan = self.fixture.plan(label)
        ids = {
            "current_state_object_id": "current-state-r2",
            "what_changed_object_id": "what-changed-r2",
            "context_pack_object_id": "context-pack-r2",
        }
        snapshot = {"source_ref": self.previous["source_id"], "snapshot_object_id": "snapshot-stable-r2",
                    "classification_assertion_id": "class-snapshot-stable-r2"}
        current_class = {
            "assertion_id": "class-current-state-r2", "sensitivity_level": "PUBLIC", "handling_tags": [],
        }
        delta_class = {
            "assertion_id": "class-what-changed-r2", "sensitivity_level": "PUBLIC", "handling_tags": [],
        }
        pack_class = {
            "assertion_id": "class-context-pack-r2", "sensitivity_level": "PUBLIC", "handling_tags": [],
        }
        relation = f"relation:{ids['current_state_object_id']}:supersedes:{self.previous['state_id']}"
        finish_command = "finish-continuation"
        extra = {
            *ids.values(), snapshot["snapshot_object_id"],
            "logical-ref:project-nexus:current-state", relation,
            "object:" + ids["current_state_object_id"], "object:" + ids["what_changed_object_id"],
            "object:" + snapshot["snapshot_object_id"],
            "evt-" + finish_command + ":verifying", "evt-" + finish_command + ":terminal",
        }
        self._extra_resources(plan, extra)
        self.current_start_plan = plan
        start_daily_task(
            store=self.store, authority=self.authority, budget=self.fixture.budget,
            trace=self.fixture.trace, runtime=self.fixture.runtime, verifier=self.fixture.verifier,
            plan=plan, confirmation=lambda *_: True,
        )
        return {
            "protocol_version": PROTOCOL_VERSION, "command_id": "continuation-r2",
            "instance_expectation": copy.deepcopy(plan["instance_expectation"]),
            "operator_principal_id": "operator-human", "task_id": plan["root"]["task_id"],
            "root_run_id": plan["root"]["root_run_id"], "grant_id": plan["grant"]["grant_id"],
            "expected_previous_state": {
                "ref_id": "project-nexus:current-state", "object_id": self.previous["state_id"],
                "integrity_sha256": self.previous["state_hash"], "current_ref_revision": None,
                "accepted_revision": self.old_revision,
            },
            "expected_previous_context": {
                "pack_ref": self.previous["pack_id"], "content_hash": self.previous["content_hash"],
                "integrity_sha256": self.previous["integrity_hash"],
            },
            "git_fact": self.git_fact,
            "human_assertions": {
                "current_objective": "Validate governed continuation for the next fresh session.",
                "next_step": "Review the committed state in a fresh read-only context.",
                "recent_work": "Implemented the accepted continuation commit workflow.",
            },
            "as_of": self._now(), "outputs": {
                **ids, "current_state_classification": current_class,
                "what_changed_classification": delta_class, "context_pack_classification": pack_class,
            },
            "stable_sources": [snapshot],
        }

    def _commit(self, plan=None, confirmation=None, stage_hook=None):
        return commit_continuation(
            store=self.store, authority=self.authority, context_packs=self.context,
            plan=plan or self.plan, git_repo=self.repo_path,
            confirmation=confirmation or (lambda *_: True), _stage_hook=stage_hook,
        )

    def _counts(self):
        with self.store._connection() as conn:
            return tuple(conn.execute(
                "SELECT (SELECT COUNT(*) FROM tasks),(SELECT COUNT(*) FROM runs),"
                "(SELECT COUNT(*) FROM delegation_grants),(SELECT COUNT(*) FROM objects),"
                "(SELECT COUNT(*) FROM context_pack_records),(SELECT COUNT(*) FROM memory_candidates),"
                "(SELECT COUNT(*) FROM admitted_memory_rows),(SELECT COUNT(*) FROM effects),"
                "(SELECT COUNT(*) FROM skill_registry_entries),(SELECT COUNT(*) FROM trace_events),"
                "(SELECT COUNT(*) FROM command_ledger),(SELECT COUNT(*) FROM value_metering_records)"
            ).fetchone())

    def test_commit_supersedes_state_compiles_fresh_context_and_exact_replay(self):
        before = self._counts()
        prepared = prepare_continuation_commit(
            store=self.store, authority=self.authority, context_packs=self.context,
            plan=self.plan, git_repo=self.repo_path,
        )
        self.assertEqual(prepared["confirmation_phrase"], "COMMIT CONTINUATION task-continuation")
        confirmations = []
        result = self._commit(confirmation=lambda phrase, summary: confirmations.append((phrase, summary)) or True)
        self.assertEqual(result["status"], "CONTINUATION_COMMITTED")
        self.assertFalse(result["replayed"])
        self.assertEqual(confirmations[0][0], "COMMIT CONTINUATION task-continuation")
        self.assertEqual(confirmations[0][1]["git_fact"]["commit_sha"], self.git_fact["commit_sha"])
        self.assertEqual(confirmations[0][1]["stable_sources"][0]["source_ref"], self.previous["source_id"])
        self.assertNotIn(str(self.repo_path), json.dumps(confirmations[0][1]))
        state = json.loads(self.store.get_payload("current-state-r2"))
        changed = json.loads(self.store.get_payload("what-changed-r2"))
        self.assertEqual(state["accepted_revision"], self.git_fact["commit_sha"])
        self.assertEqual(state["operator_accepted_work_outcome"]["value"], "SUCCEEDED")
        self.assertEqual(state["task_run_lifecycle_at_commit"]["task_status"], "ACTIVE")
        self.assertEqual(state["task_run_lifecycle_at_commit"]["root_run_status"], "RUNNING")
        self.assertEqual(state["supersedes"]["object_id"], self.previous["state_id"])
        self.assertEqual(state["previous_context"]["pack_ref"], self.previous["pack_id"])
        self.assertEqual(changed["accepted_revision"], {"before": self.old_revision, "after": self.git_fact["commit_sha"]})
        self.assertIn(self.previous["state_id"], changed["facts_superseded"])
        self.assertEqual(changed["recent_work_added"]["outcome_source"], "HUMAN_OPERATOR_ASSERTION")
        self.assertEqual(self.store.get_object_metadata(self.previous["state_id"])["payload_state"], "AVAILABLE")
        with self.store._connection() as conn:
            task = conn.execute("SELECT status FROM tasks WHERE task_id='task-continuation'").fetchone()[0]
            run = conn.execute("SELECT status FROM runs WHERE run_id='run-continuation'").fetchone()[0]
            current = conn.execute("SELECT current_object_id,revision FROM logical_refs WHERE ref_id='project-nexus:current-state'").fetchone()
            grant = conn.execute("SELECT status FROM delegation_grants WHERE grant_id='grant-continuation'").fetchone()[0]
        self.assertEqual((task, run, tuple(current), grant), ("ACTIVE", "RUNNING", ("current-state-r2", 2), "ACTIVE"))
        latest = self.context.read_latest_compiled()
        self.assertEqual(latest["pack_id"], "context-pack-r2")
        self.assertEqual(latest["model_visible_exposure"], "UNKNOWN")
        self.assertEqual({entry["source_ref"] for entry in latest["entries"]}, {
            "current-state-r2", "what-changed-r2", "snapshot-stable-r2",
        })
        snapshot = next(entry for entry in latest["entries"] if entry["source_ref"] == "snapshot-stable-r2")
        snapshot_doc = json.loads(snapshot["content"])
        self.assertEqual(snapshot_doc["schema_id"], "nexus.continuation_snapshot_entry")
        self.assertEqual(snapshot_doc["source_ref"], self.previous["source_id"])
        self.assertEqual(snapshot_doc["content"], "Stable accepted product boundary.\n")
        self.assertEqual(self.context.read_compiled(self.previous["pack_id"])["pack_id"], self.previous["pack_id"])
        self.assertEqual(state["previous_context"]["content_hash"], self.previous["content_hash"])
        with self.store._connection() as conn:
            self.assertTrue(conn.execute(
                "SELECT 1 FROM object_relations WHERE from_id='current-state-r2' "
                "AND relation_type='supersedes' AND to_id=?", (self.previous["state_id"],),
            ).fetchone())
        after_first = self._counts()
        self.assertEqual(after_first[11], before[11] + 1)
        self.assertEqual(after_first[9], before[9])
        self.assertEqual(after_first[2], before[2])
        replay = self._commit(confirmation=lambda *_: self.fail("exact bound retry must not reprompt"))
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay, result | {"replayed": True})
        self.assertEqual(self._counts(), after_first)
        self.assertEqual(self._counts()[0:2], before[0:2])
        self.assertEqual(after_first[4], before[4] + 1)
        self.assertEqual(after_first[5:10], before[5:10])
        self.assertGreater(after_first[10], before[10])
        self.assertEqual(after_first[3], before[3] + 4)

    def test_denied_confirmation_and_non_tty_have_no_canonical_mutation(self):
        before = self._counts()
        with self.assertRaises(ContinuationCommitError) as caught:
            self._commit(confirmation=lambda *_: False)
        self.assertEqual(caught.exception.reason_code, "CONTINUATION_CONFIRMATION_DENIED")
        self.assertEqual(self._counts(), before)

    def test_confirmation_phrase_requires_exact_tty_input(self):
        class TTYBuffer(io.StringIO):
            def isatty(self):
                return True

        phrase = "COMMIT CONTINUATION task-continuation"
        stdin = TTYBuffer(phrase + "\n")
        stdout = TTYBuffer()
        with mock.patch("sys.stdin", stdin), mock.patch("sys.stdout", stdout):
            self.assertTrue(_confirm_continuation(phrase, {"task_id": "task-continuation"}))
        self.assertIn("Type COMMIT CONTINUATION task-continuation to confirm:", stdout.getvalue())
        with mock.patch("sys.stdin", TTYBuffer(phrase + " extra\n")), mock.patch("sys.stdout", TTYBuffer()):
            self.assertFalse(_confirm_continuation(phrase, {"task_id": "task-continuation"}))

    def test_request_binding_crash_resumes_without_second_confirmation(self):
        with self.assertRaises(ContinuationCommitError) as caught:
            self._commit(stage_hook=lambda stage: (_ for _ in ()).throw(RuntimeError(stage))
                         if stage == "request_bound" else None)
        self.assertEqual(caught.exception.reason_code, "CONTINUATION_COMMIT_FAILED")
        before_retry = self._counts()
        (self.repo_path / "README.md").write_text("changed after durable request binding\n", encoding="utf-8")
        result = self._commit(confirmation=lambda *_: self.fail("durable request must not reprompt"))
        self.assertEqual(result["status"], "CONTINUATION_COMMITTED")
        self.assertEqual(self._counts()[0:2], before_retry[0:2])

    def test_bound_exact_replay_does_not_reread_changed_git_worktree(self):
        self._commit()
        (self.repo_path / "README.md").write_text("changed after completed commit\n", encoding="utf-8")
        replay = self._commit(confirmation=lambda *_: self.fail("completed bound replay must not prompt"))
        self.assertTrue(replay["replayed"])

    def test_task_finish_remains_separate_and_closes_the_original_grant(self):
        committed = self._commit()
        state_before_finish = json.loads(self.store.get_payload(committed["new_current_state_ref"]))
        finish = self._finish_plan(self.current_start_plan, "finish-continuation")
        result = finish_daily_task(
            store=self.store, authority=self.authority, trace=self.fixture.trace,
            plan=finish, confirmation=lambda *_: True,
        )
        self.assertEqual(result["status"], "DAILY_TASK_FINISHED")
        self.assertEqual((result["task_status"], result["run_status"], result["grant_status"]),
                         ("SUCCEEDED", "SUCCEEDED", "REVOKED"))
        state_after_finish = json.loads(self.store.get_payload(committed["new_current_state_ref"]))
        self.assertEqual(state_after_finish, state_before_finish)
        self.assertEqual(state_after_finish["task_run_lifecycle_at_commit"], {
            "task_id": "task-continuation", "root_run_id": "run-continuation",
            "task_status": "ACTIVE", "root_run_status": "RUNNING",
        })
        self.assertEqual(self.context.read_latest_compiled()["pack_id"], committed["context_pack_ref"])

    def test_finish_before_commit_and_unresolved_effect_fail_closed(self):
        finish = self._finish_plan(self.current_start_plan, "finish-continuation")
        finish_daily_task(
            store=self.store, authority=self.authority, trace=self.fixture.trace,
            plan=finish, confirmation=lambda *_: True,
        )
        before = self._counts()
        with self.assertRaises(ContinuationCommitError) as caught:
            self._commit(confirmation=lambda *_: self.fail("terminal task must fail before confirmation"))
        self.assertEqual(caught.exception.reason_code, "CONTINUATION_TASK_NOT_ACTIVE")
        self.assertEqual(self._counts(), before)

        # The effect-closure guard is exercised without adding an Effect fixture.
        isolated = type(self)()
        isolated.setUp()
        try:
            before_effect_check = isolated._counts()
            with mock.patch("adapters.client.continuation._check_work_closure",
                            side_effect=ContinuationCommitError("DAILY_TASK_EFFECTS_UNRESOLVED")):
                with self.assertRaises(ContinuationCommitError) as effect_error:
                    isolated._commit(confirmation=lambda *_: self.fail("unsafe effect state must fail before confirmation"))
            self.assertEqual(effect_error.exception.reason_code, "DAILY_TASK_EFFECTS_UNRESOLVED")
            self.assertEqual(isolated._counts(), before_effect_check)
        finally:
            isolated.doCleanups()

    def test_stale_previous_state_and_expired_grant_fail_before_confirmation(self):
        before = self._counts()
        stale_state = copy.deepcopy(self.plan)
        stale_state["expected_previous_state"]["integrity_sha256"] = "0" * 64
        with self.assertRaises(ContinuationCommitError) as caught:
            self._commit(stale_state, confirmation=lambda *_: self.fail("stale state must fail preflight"))
        self.assertEqual(caught.exception.reason_code, "CONTINUATION_PREVIOUS_STATE_NOT_IN_CONTEXT")
        self.assertEqual(self._counts(), before)

        real_datetime = datetime
        class FutureDateTime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return real_datetime.now(tz) + timedelta(days=3)
        with mock.patch("adapters.client.continuation.datetime", FutureDateTime):
            with self.assertRaises(ContinuationCommitError) as caught:
                self._commit(confirmation=lambda *_: self.fail("expired grant must fail before confirmation"))
        self.assertEqual(caught.exception.reason_code, "CONTINUATION_AUTHORITY_UNAVAILABLE")
        self.assertEqual(self._counts(), before)

    def test_revoked_grant_fails_before_confirmation_without_further_mutation(self):
        isolated = type(self)()
        isolated.setUp()
        try:
            isolated.authority.revoke_grant("grant-continuation", "fixture-revoke-continuation")
            before = isolated._counts()
            with self.assertRaises(ContinuationCommitError) as caught:
                isolated._commit(confirmation=lambda *_: self.fail("revoked Grant must fail before confirmation"))
            self.assertEqual(caught.exception.reason_code, "CONTINUATION_AUTHORITY_UNAVAILABLE")
            self.assertEqual(isolated._counts(), before)
        finally:
            isolated.doCleanups()

    def test_wrong_root_and_cli_non_tty_fail_without_writer_mutation(self):
        wrong_root = copy.deepcopy(self.plan)
        wrong_root["root_run_id"] = "run-other"
        with self.assertRaises(ContinuationCommitError) as caught:
            self._commit(wrong_root, confirmation=lambda *_: self.fail("wrong root must fail before confirmation"))
        self.assertEqual(caught.exception.reason_code, "CONTINUATION_TASK_BINDING_MISMATCH")

        plan_path = self.base / "continuation-plan.json"
        plan_path.write_text(json.dumps(self.plan, ensure_ascii=False), encoding="utf-8")
        before_counts = self._counts()
        self.store.close()
        before_fs = self._filesystem_commitment()
        output, error = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("")), mock.patch("sys.stdout", output), mock.patch("sys.stderr", error):
            status = client_main([
                "--data-root", str(self.fixture.root), "--policy", str(self.fixture.policy_path),
                "--independent-purge-journal", str(self.fixture.journal),
                "continuation", "commit", "--plan", str(plan_path), "--repo", str(self.repo_path),
            ])
        self.assertEqual(status, 2)
        self.assertIn("INTERACTIVE_TTY_REQUIRED", error.getvalue())
        self.assertNotIn(str(self.base), output.getvalue() + error.getvalue())
        self.assertNotIn("PRIVATE_INPUT_MARKER", output.getvalue() + error.getvalue())
        self.assertEqual(self._filesystem_commitment(), before_fs)
        self.assertEqual(before_counts[0:3], (2, 2, 2))

    def _filesystem_commitment(self):
        result = {}
        for label, root in (("data", self.fixture.root), ("journal", self.fixture.journal.parent)):
            for path in sorted(item for item in root.rglob("*") if item.is_file()):
                relative = path.relative_to(root).as_posix()
                payload = path.read_bytes()
                result[f"{label}/{relative}"] = (len(payload), hashlib.sha256(payload).hexdigest())
        return result

    def test_partial_boundaries_resume_exactly_without_duplicate_pack_or_outputs(self):
        for stage in ("current_state_written", "what_changed_written", "supersession_written",
                      "current_ref_initialized", "current_ref_updated", "context_classified", "context_compiled"):
            with self.subTest(stage=stage):
                # Each boundary uses a separate disposable Core fixture.
                isolated = type(self)()
                isolated.setUp()
                try:
                    before = isolated._counts()
                    with self.assertRaises(ContinuationCommitError):
                        isolated._commit(stage_hook=lambda current, target=stage: (_ for _ in ()).throw(RuntimeError(current))
                                         if current == target else None)
                    first_retry_counts = isolated._counts()
                    resumed = isolated._commit(confirmation=lambda *_: self.fail("bound partial request must not reprompt"))
                    self.assertEqual(resumed["status"], "CONTINUATION_COMMITTED")
                    final = isolated._counts()
                    self.assertEqual(final[0:2], before[0:2])
                    self.assertEqual(final[4], before[4] + 1)
                    self.assertEqual(final[5:10], before[5:10])
                    self.assertEqual(final[11], before[11] + 1)
                    self.assertGreaterEqual(first_retry_counts[3], before[3])
                finally:
                    isolated.doCleanups()

    def test_stale_git_revision_and_wrong_task_fail_before_confirmation(self):
        before = self._counts()
        changed_git = copy.deepcopy(self.plan)
        changed_git["git_fact"]["commit_sha"] = self.old_revision
        called = []
        with self.assertRaises(ContinuationCommitError) as caught:
            self._commit(changed_git, confirmation=lambda *_: called.append(True) or True)
        self.assertEqual(caught.exception.reason_code, "CONTINUATION_GIT_FACT_MISMATCH")
        self.assertEqual(called, [])
        self.assertEqual(self._counts(), before)

        wrong_task = copy.deepcopy(self.plan)
        wrong_task["task_id"] = "task-other"
        with self.assertRaises(ContinuationCommitError):
            self._commit(wrong_task, confirmation=lambda *_: self.fail("wrong task must fail preflight"))
        self.assertEqual(self._counts(), before)

    def test_wrong_instance_and_missing_resource_fail_before_confirmation(self):
        before = self._counts()
        wrong = copy.deepcopy(self.plan)
        wrong["instance_expectation"]["instance_id"] = "wrong-instance"
        with self.assertRaises(ContinuationCommitError) as caught:
            self._commit(wrong, confirmation=lambda *_: self.fail("wrong instance must fail before confirmation"))
        self.assertEqual(caught.exception.reason_code, "CONTINUATION_INSTANCE_BINDING_MISMATCH")
        self.assertEqual(self._counts(), before)

        # Test a declared Grant that omits one exact resource without mutating it.
        required_plan = copy.deepcopy(self.plan)
        required_plan["outputs"]["current_state_object_id"] = "unscoped-current"
        with self.assertRaises(ContinuationCommitError) as caught:
            self._commit(required_plan, confirmation=lambda *_: self.fail("scope closure must fail before confirmation"))
        self.assertEqual(caught.exception.reason_code, "CONTINUATION_AUTHORITY_SCOPE_INSUFFICIENT")
        self.assertEqual(self._counts(), before)

    def test_forbidden_action_and_wildcard_scopes_fail_closed(self):
        original = continuation_module._read_projection
        before = self._counts()
        for scope_key, value, expected in (
            ("action_scope", "DELEGATE", "CONTINUATION_AUTHORITY_SCOPE_INSUFFICIENT"),
            ("resource_scope", "*", "CONTINUATION_AUTHORITY_INVALID"),
            ("audience_scope", "*", "CONTINUATION_AUTHORITY_INVALID"),
        ):
            with self.subTest(scope=scope_key):
                def altered(store, normalized, key=scope_key, item=value):
                    projection = original(store, normalized)
                    projection["scope"][key] = sorted([*projection["scope"][key], item])
                    return projection

                with mock.patch("adapters.client.continuation._read_projection", side_effect=altered):
                    with self.assertRaises(ContinuationCommitError) as caught:
                        self._commit(confirmation=lambda *_: self.fail("unsafe authority must fail before confirmation"))
                self.assertEqual(caught.exception.reason_code, expected)
                self.assertEqual(self._counts(), before)

    def test_child_command_collision_fails_before_confirmation(self):
        plan = copy.deepcopy(self.plan)
        plan["command_id"] = "continuation-collision"
        self.store.bind_command_request(
            command_id="continuation-collision:put-current-state",
            operation="unrelated_fixture_request", request={"fixture": True},
        )
        before = self._counts()
        with self.assertRaises(ContinuationCommitError) as caught:
            self._commit(plan, confirmation=lambda *_: self.fail("child command collision must preflight-deny"))
        self.assertEqual(caught.exception.reason_code, "CONTINUATION_ID_CONFLICT")
        self.assertEqual(self._counts(), before)

    def test_plan_reader_rejects_duplicate_keys_and_exact_replay_conflict(self):
        path = self.base / "duplicate-plan.json"
        path.write_text('{"protocol_version":"x","protocol_version":"y"}', encoding="utf-8")
        with self.assertRaises(ContinuationCommitError):
            read_continuation_plan(path)

        self._commit()
        changed = copy.deepcopy(self.plan)
        changed["human_assertions"]["next_step"] += " changed"
        with self.assertRaises(ContinuationCommitError):
            self._commit(changed, confirmation=lambda *_: self.fail("command conflict must be detected before prompt"))

    def test_human_assertions_reject_embedded_absolute_paths(self):
        before = self._counts()
        for value in (
            "The handoff is at (C:\\private\\handoff.txt).",
            "The notes are in /private/project/notes.md.",
            "The share is \\\\server\\private\\handoff.txt.",
        ):
            with self.subTest(value=value):
                changed = copy.deepcopy(self.plan)
                changed["human_assertions"]["recent_work"] = value
                with self.assertRaises(ContinuationCommitError) as caught:
                    self._commit(changed, confirmation=lambda *_: self.fail("path-bearing assertion must fail preflight"))
                self.assertEqual(caught.exception.reason_code, "CONTINUATION_HUMAN_ASSERTION_INVALID")
                self.assertEqual(self._counts(), before)


if __name__ == "__main__":
    unittest.main()
