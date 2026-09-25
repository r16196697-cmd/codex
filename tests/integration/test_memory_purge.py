import json
import hashlib
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

from adapters.storage import ObjectStore
from adapters.client.operator import OperatorClient
from kernel.authority.errors import AuthorizationDenied
from kernel.object.errors import CommandConflict, PurgedObject, PurgeBarrierActive
from kernel.authority import AuthorityService
from kernel.budget import BudgetService
from kernel.memory import MemoryService
from kernel.purge import PurgeService
from kernel.runtime.errors import RuntimeDenied
from kernel.runtime import RuntimeModeService
from kernel.run import TraceRuntime
from kernel.verification import VerificationService
from kernel.runtime.inspect import InspectService


class MemoryPurgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nexus-step7-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data_root = self.root / "data"
        self.store = ObjectStore(self.data_root)
        self.addCleanup(self.store.close)
        policy = json.loads((Path(__file__).resolve().parents[2] / "policies" / "default-policy.json").read_text(encoding="utf-8"))
        policy["trust_anchors"] = ["human-root"]
        self.authority = AuthorityService(self.store, policy)
        for principal_id, principal_type in (("human-root", "HUMAN"), ("agent", "SERVICE")):
            self.authority.register_principal({"schema_id": "nexus.principal", "schema_version": 1, "principal_id": principal_id, "principal_type": principal_type, "status": "ACTIVE"}, "principal-" + principal_id)
        self.authority.register_trust_anchor({"schema_id": "nexus.trust_anchor", "schema_version": 1, "anchor_id": "anchor-root", "principal_id": "human-root", "policy_ref": "1"}, "anchor-root")
        self.actions = ["RUN_CREATE", "TRACE_APPEND", "VERIFY", "MEMORY_RETAIN", "MEMORY_ADMIT", "MEMORY_SEARCH", "PURGE_EXECUTE"]
        self.resources = ["run-7", "claim-7", "evidence-7", "manifest-7", "candidate-7", "candidate-quarantine", "candidate-human", "candidate-human-conflict", "plan-7", "record-7", "evt-purge-ref-event"]
        now = datetime.now(timezone.utc)
        self.authority.create_grant({"schema_id": "nexus.delegation_grant", "schema_version": 1, "grant_id": "grant-7", "issued_by": "human-root", "granted_to": "agent", "task_scope": ["task-7"], "resource_scope": self.resources, "action_scope": self.actions, "audience_scope": ["nexus-runtime"], "issued_at": now.isoformat(), "expires_at": (now + timedelta(days=1)).isoformat(), "status": "ACTIVE", "policy_version": "1"}, "grant-7")
        self._seed_task_run()
        self.claim = self._put("claim-7", b"The synthetic Nexus fact is governed memory.")
        self.evidence = self._put("evidence-7", b"Independent synthetic evidence for governed memory.")
        self._bind_run_manifest()
        self._seed_pending_run()
        self.verifier = VerificationService(self.store, self.authority)
        self.memory = MemoryService(self.store, self.authority, self.verifier)
        self.journal_path = self.root / "independent" / "purge.jsonl"
        self.purge = PurgeService(self.store, self.authority, self.memory, independent_journal_path=self.journal_path)

    def _seed_task_run(self):
        now = datetime.now(timezone.utc).isoformat()
        with self.store._connection() as conn:
            conn.execute("INSERT INTO tasks(task_id,requester_id,status,created_at,command_id,root_run_id) VALUES('task-7','human-root','CREATED',?,'task-7',NULL)", (now,))
        self._classify("class-run-7", "RUN", "run-7")
        with self.store._connection() as conn:
            conn.execute("INSERT INTO runs(run_id,task_id,subtask_id,parent_run_id,executor_kind,status,grant_id,manifest_ref,budget_reservation_ref,data_boundary_json,classification_assertion_ref,created_at) VALUES('run-7','task-7',NULL,NULL,'ORCHESTRATOR','CREATED','grant-7',NULL,NULL,?,?,?)", (json.dumps({"allowed_classifications": ["PUBLIC"], "handling_tags": []}), "class-run-7", now))

    def _bind_run_manifest(self):
        manifest = {"schema_id": "nexus.run_manifest", "schema_version": 1, "executor_kind": "ORCHESTRATOR", "runtime_version": "0.1", "policy_version": "1", "schema_versions": {"nexus.run_manifest": 1}, "input_object_refs": ["claim-7"], "authority_grant_ref": "grant-7", "data_boundary": {"allowed_classifications": ["PUBLIC"], "handling_tags": []}, "classification_assertion_ref": "class-run-7", "task_contract_ref": "contract-7", "dag_version": "1", "scheduler_version": "1"}
        self._classify("class-manifest-7", "OBJECT", "manifest-7")
        self.store.put_object(command_id="manifest-7", object_id="manifest-7", payload=json.dumps(manifest).encode(), object_type="run_manifest", created_by_run="run-7", classification_assertion_ref="class-manifest-7")
        with self.store._connection() as conn:
            conn.execute("UPDATE runs SET manifest_ref='manifest-7' WHERE run_id='run-7'")
            conn.execute("UPDATE runs SET status='READY' WHERE run_id='run-7'")
            conn.execute("UPDATE runs SET status='RUNNING' WHERE run_id='run-7'")
            conn.execute("UPDATE runs SET status='VERIFYING' WHERE run_id='run-7'")

    def _seed_pending_run(self):
        now = datetime.now(timezone.utc).isoformat()
        self._classify("class-run-pending", "RUN", "run-pending")
        with self.store._connection() as conn:
            conn.execute("INSERT INTO runs(run_id,task_id,subtask_id,parent_run_id,executor_kind,status,grant_id,manifest_ref,budget_reservation_ref,data_boundary_json,classification_assertion_ref,created_at) VALUES('run-pending','task-7','subtask-pending','run-7','TOOL','CREATED','grant-7',NULL,NULL,?,?,?)", (json.dumps({"allowed_classifications": ["PUBLIC"], "handling_tags": []}), "class-run-pending", now))
        manifest = {"schema_id": "nexus.run_manifest", "schema_version": 1, "executor_kind": "TOOL", "runtime_version": "0.1", "policy_version": "1", "schema_versions": {"nexus.run_manifest": 1}, "input_object_refs": ["claim-7"], "authority_grant_ref": "grant-7", "data_boundary": {"allowed_classifications": ["PUBLIC"], "handling_tags": []}, "classification_assertion_ref": "class-run-pending", "tool_id": "fake-read", "tool_descriptor_version": "1", "tool_adapter_version": "test", "input_ref": "claim-7"}
        self._classify("class-manifest-pending", "OBJECT", "manifest-pending")
        self.store.put_object(command_id="manifest-pending", object_id="manifest-pending", payload=json.dumps(manifest).encode(), object_type="run_manifest", created_by_run="run-pending", classification_assertion_ref="class-manifest-pending")
        with self.store._connection() as conn:
            conn.execute("UPDATE runs SET manifest_ref='manifest-pending' WHERE run_id='run-pending'")

    def _classify(self, assertion_id, subject_type, subject_ref):
        with self.store._connection() as conn:
            conn.execute("INSERT INTO classification_assertions(assertion_id,subject_type,subject_ref,sensitivity_level,handling_tags_json,policy_version,reason,actor_id) VALUES(?,?,?,'PUBLIC','[]','1','synthetic Step 7 fixture','agent')", (assertion_id, subject_type, subject_ref))

    def _put(self, object_id, payload):
        class_ref = "class-" + object_id
        self._classify(class_ref, "OBJECT", object_id)
        return self.store.put_object(command_id="put-" + object_id, object_id=object_id, payload=payload, object_type="artifact", created_by_run="run-7", classification_assertion_ref=class_ref)

    def _approve_purge(self, plan_hash):
        now = datetime.now(timezone.utc).isoformat()
        self.authority.create_approval({"schema_id": "nexus.approval_decision", "schema_version": 1, "approval_id": "approval-7", "approver_principal_id": "human-root", "target_type": "PURGE_EXECUTE", "target_ref": "plan-7", "effect_id": "record-7", "payload_integrity_hash": plan_hash, "decision": "APPROVE", "approved_scope": ["PURGE_EXECUTE", "plan-7"], "policy_version": "1", "issued_at": now}, "approval-7")

    def _human_verification(self, verification_id):
        axes = {"generator_independence": "NOT_APPLICABLE", "evidence_independence": "INDEPENDENT", "method_independence": "INDEPENDENT"}
        payload_hash = self.verifier.human_payload_hash(verification_id=verification_id, target_ref=self.claim, evidence_refs=[self.evidence], run_id="run-7", independence=axes)
        approval_id = "approval-" + verification_id
        now = datetime.now(timezone.utc).isoformat()
        self.authority.create_approval({"schema_id": "nexus.approval_decision", "schema_version": 1, "approval_id": approval_id, "approver_principal_id": "human-root", "target_type": "VERIFY", "target_ref": self.claim, "effect_id": verification_id, "payload_integrity_hash": payload_hash, "decision": "APPROVE", "approved_scope": ["VERIFY", self.claim], "policy_version": "1", "issued_at": now}, approval_id)
        return self.verifier.record_human_verification(verification_id=verification_id, target_ref=self.claim, evidence_refs=[self.evidence], run_id="run-7", approval_id=approval_id, attester_principal_id="human-root", independence=axes)

    def test_t1_verification_admission_quarantine_and_separate_fts(self):
        self.memory.retain_raw(command_id="retain-raw", object_id=self.claim, run_id="run-7")
        result = self.verifier.verify_object_integrity(verification_id="verify-pass", target_ref=self.claim, evidence_refs=[self.evidence], run_id="run-7")
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(set(result["independence"]), {"generator_independence", "evidence_independence", "method_independence"})
        candidate = self.memory.create_candidate(command_id="candidate-admit", candidate_id="candidate-7", claim_ref=self.claim, evidence_refs=[self.evidence], owner="agent", classification_assertion_ref="class-claim-7", verification_ref="verify-pass", review_trigger="new contradictory evidence")
        self.assertEqual(candidate["status"], "QUARANTINED")
        self.assertEqual(candidate["truth_state"], "INFERRED")
        self.assertEqual([row["object_id"] for row in self.memory.search_raw(query="governed memory", run_id="run-7")], [self.claim])
        with self.store._connection() as conn:
            self.assertNotIn("body", {row["name"] for row in conn.execute("PRAGMA table_info(raw_history_rows)")})
            self.assertNotIn("body", {row["name"] for row in conn.execute("PRAGMA table_info(admitted_memory_rows)")})
        self.assertEqual(self.memory.search_admitted(query="governed memory", run_id="run-7"), [])
        human_result = self._human_verification("verify-human")
        admitted = self.memory.create_candidate(command_id="candidate-human", candidate_id="candidate-human", claim_ref=self.claim, evidence_refs=[self.evidence], owner="agent", classification_assertion_ref="class-claim-7", verification_ref=human_result["verification_id"], review_trigger="contradiction or expiry")
        self.assertEqual((admitted["status"], admitted["truth_state"]), ("ADMITTED", "VERIFIED"))
        self.assertEqual(len(self.memory.search_admitted(query="governed memory", run_id="run-7")), 1)
        conflicted = self.memory.create_candidate(command_id="candidate-human-conflict", candidate_id="candidate-human-conflict", claim_ref=self.claim, evidence_refs=[self.evidence], owner="agent", classification_assertion_ref="class-claim-7", verification_ref=human_result["verification_id"], review_trigger="resolve contradictory source", conflicts=[self.evidence])
        self.assertEqual((conflicted["status"], conflicted["truth_state"]), ("QUARANTINED", "UNKNOWN"))
        self.assertEqual(conflicted["conflicts"], [self.evidence])
        with self.assertRaises(RuntimeDenied):
            self.verifier.verify_object_integrity(verification_id="verify-lying-axes", target_ref=self.claim, evidence_refs=[self.evidence], run_id="run-7", independence={"generator_independence": "INDEPENDENT", "evidence_independence": "INDEPENDENT", "method_independence": "INDEPENDENT"})

    def test_t3_approval_is_bound_to_exact_verification_payload(self):
        axes = {"generator_independence": "NOT_APPLICABLE", "evidence_independence": "INDEPENDENT", "method_independence": "INDEPENDENT"}
        payload_hash = self.verifier.human_payload_hash(verification_id="verify-human-bad", target_ref=self.claim, evidence_refs=[self.evidence], run_id="run-7", independence=axes)
        now = datetime.now(timezone.utc).isoformat()
        self.authority.create_approval({"schema_id": "nexus.approval_decision", "schema_version": 1, "approval_id": "approval-human-bad", "approver_principal_id": "human-root", "target_type": "VERIFY", "target_ref": self.claim, "effect_id": "verify-human-bad", "payload_integrity_hash": "0" * 64, "decision": "APPROVE", "approved_scope": ["VERIFY", self.claim], "policy_version": "1", "issued_at": now}, "approval-human-bad")
        self.assertNotEqual(payload_hash, "0" * 64)
        with self.assertRaises(AuthorizationDenied):
            self.verifier.record_human_verification(verification_id="verify-human-bad", target_ref=self.claim, evidence_refs=[self.evidence], run_id="run-7", approval_id="approval-human-bad", attester_principal_id="human-root", independence=axes)

    def test_barrier_stays_partial_for_active_run_and_unknown_effect(self):
        self._classify("class-manifest-late", "OBJECT", "manifest-late")
        self.memory.retain_raw(command_id="retain-before-partial", object_id=self.claim, run_id="run-7")
        result = self._human_verification("verify-for-partial")
        self.memory.create_candidate(command_id="candidate-before-partial", candidate_id="candidate-7", claim_ref=self.claim, evidence_refs=[self.evidence], owner="agent", classification_assertion_ref="class-claim-7", verification_ref=result["verification_id"], review_trigger="review")
        self.assertEqual(len(self.memory.search_admitted(query="governed memory", run_id="run-7")), 1)
        self._seed_unknown_effect()
        plan = self.purge.plan(command_id="purge-plan-partial", plan_id="plan-7", task_id="task-7", target_refs=[self.claim])
        self._approve_purge(plan["plan_hash"])
        outcome = self.purge.execute(command_id="purge-execute-partial", record_id="record-7", barrier_id="barrier-7", plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7", quiesce_run=lambda _: True)
        self.assertEqual(outcome["status"], "PARTIAL")
        self.assertIn("UNKNOWN_EFFECT:effect-unknown-7", outcome["unresolved_items"])
        self.assertTrue(any(item.startswith("ACTIVE_RUN:") for item in outcome["unresolved_items"]))
        now = datetime.now(timezone.utc)
        self.authority.create_grant({"schema_id":"nexus.delegation_grant","schema_version":1,"grant_id":"inspect-purge-7","issued_by":"human-root","granted_to":"agent","task_scope":["task-7"],"resource_scope":["purge:plan-7"],"action_scope":["INSPECT"],"audience_scope":["nexus-inspect"],"issued_at":now.isoformat(),"expires_at":(now+timedelta(days=1)).isoformat(),"status":"ACTIVE","policy_version":"1"}, "inspect-purge-grant-7")
        with self.store._connection() as conn:
            conn.execute("UPDATE tasks SET root_run_id='run-7',status='ACTIVE' WHERE task_id='task-7'")
        purge_view = InspectService(self.store, self.authority).purge(grant_id="inspect-purge-7", task_id="task-7", plan_id="plan-7")
        self.assertEqual(purge_view["executions"][0]["status"], "PARTIAL")
        self.assertIn("UNKNOWN_EFFECT:effect-unknown-7", purge_view["executions"][0]["unresolved"])
        self.assertEqual(purge_view["barriers"][0]["status"], "PARTIAL")
        class PurgeInspectRuntime:
            def inspect_purge(inner_self, **kwargs):
                return InspectService(self.store, self.authority).purge(**kwargs)
        client_purge_view = OperatorClient(PurgeInspectRuntime()).inspect_purge(grant_id="inspect-purge-7", task_id="task-7", plan_id="plan-7")
        self.assertIn("PARTIAL", client_purge_view["display_state"])
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM purge_barriers WHERE barrier_id='barrier-7'").fetchone()[0], "PARTIAL")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE runs SET status='READY' WHERE run_id='run-pending'")
        late_manifest = {"schema_id": "nexus.run_manifest", "schema_version": 1, "executor_kind": "TOOL", "runtime_version": "0.1", "policy_version": "1", "schema_versions": {"nexus.run_manifest": 1}, "input_object_refs": [self.claim], "authority_grant_ref": "grant-7", "data_boundary": {"allowed_classifications": ["PUBLIC"], "handling_tags": []}, "classification_assertion_ref": "class-run-pending", "tool_id": "fake-read", "tool_descriptor_version": "1", "tool_adapter_version": "test", "input_ref": self.claim}
        with self.assertRaises(PurgeBarrierActive):
            self.store.put_object(command_id="late-manifest", object_id="manifest-late", payload=json.dumps(late_manifest).encode(), object_type="run_manifest", created_by_run="run-pending", classification_assertion_ref="class-manifest-late")
        self.assertEqual(self.memory.search_raw(query="governed memory", run_id="run-7"), [])
        self.assertEqual(self.memory.search_admitted(query="governed memory", run_id="run-7"), [])
        with self.assertRaises(PurgeBarrierActive):
            self.memory.retain_raw(command_id="retain-during-barrier", object_id=self.claim, run_id="run-7")
        self.assertEqual(self.store.get_payload(self.claim), b"The synthetic Nexus fact is governed memory.")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM purge_ledger").fetchone()[0], 2)
        replay = self.purge.replay_independent_journal()
        self.assertFalse(replay["normal_allowed"])
        self.assertEqual(replay["held_refs"], 3)
        self.assertEqual(self.store.get_payload(self.claim), b"The synthetic Nexus fact is governed memory.")

    def test_crash_after_independent_barrier_record_restores_conservative_purge_hold(self):
        self.memory.retain_raw(command_id="retain-before-journal-crash", object_id=self.claim, run_id="run-7")
        plan = self.purge.plan(command_id="journal-crash-plan-command", plan_id="plan-7", task_id="task-7", target_refs=[self.claim])
        self._approve_purge(plan["plan_hash"])
        with mock.patch.object(self.purge, "_install_barrier", side_effect=RuntimeError("SIMULATED_CRASH_AFTER_JOURNAL")):
            with self.assertRaisesRegex(RuntimeError, "SIMULATED_CRASH_AFTER_JOURNAL"):
                self.purge.execute(command_id="journal-crash-execute", record_id="record-7", barrier_id="barrier-7", plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7")
        with self.store._connection() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM purge_barriers WHERE barrier_id='barrier-7'").fetchone())

        replay = self.purge.replay_independent_journal()
        self.assertFalse(replay["normal_allowed"])
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM purge_barriers WHERE barrier_id='barrier-7'").fetchone()[0], "PARTIAL")
            self.assertEqual(replay["held_refs"], conn.execute("SELECT COUNT(*) FROM purge_barrier_refs WHERE barrier_id='barrier-7'").fetchone()[0])
        self.assertEqual(self.memory.search_raw(query="governed memory", run_id="run-7"), [])
        with self.assertRaises(PurgeBarrierActive):
            self.memory.retain_raw(command_id="retain-during-recovered-barrier", object_id=self.claim, run_id="run-7")

        self.store.close()
        self.store = ObjectStore(self.data_root)
        self.addCleanup(self.store.close)
        self.authority = AuthorityService(self.store, self.authority.policy)
        self.memory = MemoryService(self.store, self.authority, VerificationService(self.store, self.authority))
        self.purge = PurgeService(self.store, self.authority, self.memory, independent_journal_path=self.journal_path)
        recovered = self.purge.replay_independent_journal()
        self.assertFalse(recovered["normal_allowed"])
        self.assertEqual(self.memory.search_raw(query="governed memory", run_id="run-7"), [])
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM purge_barriers WHERE barrier_id='barrier-7'").fetchone()[0], "PARTIAL")

    def test_purge_delete_transaction_failure_keeps_barrier_and_same_command_completes_after_reopen(self):
        self.memory.retain_raw(command_id="retain-before-delete-crash", object_id=self.claim, run_id="run-7")
        verification = self._human_verification("verify-before-delete-crash")
        self.memory.create_candidate(command_id="candidate-before-delete-crash", candidate_id="candidate-7", claim_ref=self.claim, evidence_refs=[self.evidence], owner="agent", classification_assertion_ref="class-claim-7", verification_ref=verification["verification_id"], review_trigger="review")
        plan = self.purge.plan(command_id="delete-crash-plan", plan_id="plan-7", task_id="task-7", target_refs=[self.claim])
        self._approve_purge(plan["plan_hash"])
        protected = sorted(set(plan["target_refs"]) | set(plan["descendant_refs"]))
        with self.store._connection() as conn:
            conn.execute("UPDATE runs SET status='SUCCEEDED' WHERE run_id='run-7'")
            conn.execute("CREATE TRIGGER fail_purge_state_update BEFORE UPDATE OF payload_state ON object_states WHEN NEW.payload_state='PURGED' BEGIN SELECT RAISE(ABORT,'SIMULATED_PURGE_DB_CRASH'); END")

        with self.assertRaisesRegex(sqlite3.IntegrityError, "SIMULATED_PURGE_DB_CRASH"):
            self.purge.execute(command_id="purge-delete-crash", record_id="record-7", barrier_id="barrier-7", plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM purge_barriers WHERE barrier_id='barrier-7'").fetchone()[0], "ACTIVE")
            self.assertEqual(conn.execute("SELECT status FROM purge_execution_records WHERE record_id='record-7'").fetchone()[0], "RUNNING")
            conn.execute("DROP TRIGGER fail_purge_state_update")
        missing_after_rollback = []
        for object_id in protected:
            metadata = self.store.get_object_metadata(object_id)
            if not self.store._payload_path(metadata["payload_uri"]).is_file():
                missing_after_rollback.append(object_id)
        self.assertTrue(missing_after_rollback, "the injected SQLite rollback occurs after filesystem unlink")
        self.assertEqual(self.memory.search_raw(query="governed memory", run_id="run-7"), [])

        self.store.close()
        self.store = ObjectStore(self.data_root)
        self.addCleanup(self.store.close)
        self.authority = AuthorityService(self.store, self.authority.policy)
        self.memory = MemoryService(self.store, self.authority, VerificationService(self.store, self.authority))
        self.purge = PurgeService(self.store, self.authority, self.memory, independent_journal_path=self.journal_path)
        outcome = self.purge.execute(command_id="purge-delete-crash", record_id="record-7", barrier_id="barrier-7", plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7")
        self.assertEqual(outcome["status"], "COMPLETED")
        for object_id in protected:
            with self.assertRaises(PurgedObject):
                self.store.get_payload(object_id)
        self.assertEqual(self.memory.search_raw(query="governed memory", run_id="run-7"), [])
        self.assertTrue(self.purge.replay_independent_journal()["normal_allowed"])
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM purge_barriers WHERE barrier_id='barrier-7'").fetchone()[0], "RELEASED")

    def test_independent_release_journal_failure_is_resumable_with_same_command(self):
        plan = self.purge.plan(command_id="release-retry-plan", plan_id="plan-7", task_id="task-7", target_refs=[self.claim])
        self._approve_purge(plan["plan_hash"])
        with self.store._connection() as conn:
            conn.execute("UPDATE runs SET status='SUCCEEDED' WHERE run_id='run-7'")

        original_append = self.purge.journal.append
        failed_release = False

        def fail_first_release(**kwargs):
            nonlocal failed_release
            if kwargs.get("action") == "BARRIER_RELEASED" and not failed_release:
                failed_release = True
                raise OSError("injected independent journal release failure")
            return original_append(**kwargs)

        with mock.patch.object(self.purge.journal, "append", side_effect=fail_first_release):
            first = self.purge.execute(
                command_id="purge-release-retry", record_id="record-7", barrier_id="barrier-7",
                plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7",
            )
            self.assertEqual(first["status"], "PARTIAL")
            with self.store._connection() as conn:
                self.assertEqual(conn.execute("SELECT status FROM purge_barriers WHERE barrier_id='barrier-7'").fetchone()[0], "PARTIAL")
                self.assertEqual(conn.execute("SELECT payload_state FROM object_states WHERE object_id=?", (self.claim,)).fetchone()[0], "PURGED")
            self.store.close()
            self.store = ObjectStore(self.data_root)
            self.addCleanup(self.store.close)
            self.authority = AuthorityService(self.store, self.authority.policy)
            self.memory = MemoryService(self.store, self.authority, VerificationService(self.store, self.authority))
            self.purge = PurgeService(self.store, self.authority, self.memory, independent_journal_path=self.journal_path)
            retried = self.purge.execute(
                command_id="purge-release-retry", record_id="record-7", barrier_id="barrier-7",
                plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7",
            )

        self.assertEqual(retried["status"], "COMPLETED")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM purge_barriers WHERE barrier_id='barrier-7'").fetchone()[0], "RELEASED")
            self.assertEqual(conn.execute("SELECT status FROM purge_execution_records WHERE record_id='record-7'").fetchone()[0], "COMPLETED")
            self.assertEqual(
                [row[0] for row in conn.execute("SELECT action FROM purge_ledger WHERE barrier_id='barrier-7' ORDER BY ledger_seq")],
                ["BARRIER_INSTALLED", "BARRIER_PARTIAL", "BARRIER_RELEASED"],
            )
        self.assertTrue(self.purge.replay_independent_journal()["normal_allowed"])

    def test_durable_release_journal_with_uncommitted_sqlite_projection_resumes_after_reopen(self):
        plan = self.purge.plan(command_id="release-projection-plan", plan_id="plan-7", task_id="task-7", target_refs=[self.claim])
        self._approve_purge(plan["plan_hash"])
        with self.store._connection() as conn:
            conn.execute("UPDATE runs SET status='SUCCEEDED' WHERE run_id='run-7'")
        original_append = self.purge.journal.append
        failed_release_write = False

        def fail_first_journal_release(**kwargs):
            nonlocal failed_release_write
            if kwargs.get("action") == "BARRIER_RELEASED" and not failed_release_write:
                failed_release_write = True
                raise OSError("injected first release journal failure")
            return original_append(**kwargs)

        with mock.patch.object(self.purge.journal, "append", side_effect=fail_first_journal_release):
            first = self.purge.execute(command_id="purge-release-projection", record_id="record-7", barrier_id="barrier-7", plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7")
        self.assertEqual(first["status"], "PARTIAL")
        # The retry durably writes RELEASED, then simulates a crash/failure
        # before SQLite can project completion.
        with mock.patch.object(self.purge, "_release", side_effect=sqlite3.OperationalError("injected completion projection failure")):
            with self.assertRaisesRegex(sqlite3.OperationalError, "completion projection"):
                self.purge.execute(command_id="purge-release-projection", record_id="record-7", barrier_id="barrier-7", plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7")
        self.store.close()
        self.store = ObjectStore(self.data_root)
        self.addCleanup(self.store.close)
        self.authority = AuthorityService(self.store, self.authority.policy)
        self.memory = MemoryService(self.store, self.authority, VerificationService(self.store, self.authority))
        self.purge = PurgeService(self.store, self.authority, self.memory, independent_journal_path=self.journal_path)
        self.purge.replay_independent_journal()
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM purge_barriers WHERE barrier_id='barrier-7'").fetchone()[0], "RELEASED")
            self.assertEqual(conn.execute("SELECT status FROM purge_execution_records WHERE record_id='record-7'").fetchone()[0], "PARTIAL")
        with self.assertRaises(PurgedObject):
            self.store.get_payload(self.claim)
        journal_rows = self.purge.journal.read()
        mismatched_rows = [dict(row, plan_hash="f" * 64) if row.get("action") == "BARRIER_RELEASED" else row for row in journal_rows]
        with mock.patch.object(self.purge.journal, "read", return_value=mismatched_rows):
            with self.assertRaisesRegex(RuntimeDenied, "PURGE_RELEASE_JOURNAL_MISMATCH"):
                self.purge.execute(command_id="purge-release-projection", record_id="record-7", barrier_id="barrier-7", plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7")
        resumed = self.purge.execute(command_id="purge-release-projection", record_id="record-7", barrier_id="barrier-7", plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7")
        self.assertEqual(resumed["status"], "COMPLETED")
        self.store.close()
        self.store = ObjectStore(self.data_root)
        self.addCleanup(self.store.close)
        self.authority = AuthorityService(self.store, self.authority.policy)
        self.memory = MemoryService(self.store, self.authority, VerificationService(self.store, self.authority))
        self.purge = PurgeService(self.store, self.authority, self.memory, independent_journal_path=self.journal_path)
        replayed = self.purge.execute(command_id="purge-release-projection", record_id="record-7", barrier_id="barrier-7", plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7")
        self.assertEqual(replayed["status"], "COMPLETED")
        self.assertEqual(sum(row.get("action") == "BARRIER_RELEASED" for row in self.purge.journal.read()), 1)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM purge_execution_records WHERE record_id='record-7'").fetchone()[0], "COMPLETED")

    def test_purge_plan_same_command_returns_original_persisted_plan(self):
        first = self.purge.plan(command_id="stable-plan-command", plan_id="stable-plan", task_id="task-7", target_refs=[self.claim])
        second = self.purge.plan(command_id="stable-plan-command", plan_id="stable-plan", task_id="task-7", target_refs=[self.claim])
        self.assertEqual(second, first)
        for changed in (
            {"plan_id":"changed-plan","task_id":"task-7","target_refs":[self.claim]},
            {"plan_id":"stable-plan","task_id":"task-8","target_refs":[self.claim]},
            {"plan_id":"stable-plan","task_id":"task-7","target_refs":[self.evidence]},
        ):
            with self.assertRaisesRegex(CommandConflict, "COMMAND_CONFLICT"):
                self.purge.plan(command_id="stable-plan-command", **changed)
        self.store.close()
        self.store = ObjectStore(self.data_root)
        self.addCleanup(self.store.close)
        self.authority = AuthorityService(self.store, self.authority.policy)
        self.memory = MemoryService(self.store, self.authority, VerificationService(self.store, self.authority))
        self.purge = PurgeService(self.store, self.authority, self.memory, independent_journal_path=self.journal_path)
        self.assertEqual(self.purge.plan(command_id="stable-plan-command", plan_id="stable-plan", task_id="task-7", target_refs=[self.claim]), first)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM purge_plan_records WHERE command_id='stable-plan-command'").fetchone()[0], 1)

    def test_inspect_denies_cross_task_purged_objects_and_unresolved_approvals(self):
        plan = self.purge.plan(command_id="inspect-purge-plan", plan_id="plan-7", task_id="task-7", target_refs=[self.claim])
        self._approve_purge(plan["plan_hash"])
        now = datetime.now(timezone.utc)
        with self.store._connection() as conn:
            conn.execute("UPDATE runs SET status='SUCCEEDED' WHERE run_id='run-7'")
        completed = self.purge.execute(command_id="inspect-purge-execute", record_id="record-7", barrier_id="barrier-7", plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7")
        self.assertEqual(completed["status"], "COMPLETED")
        self.authority.create_grant({"schema_id":"nexus.delegation_grant","schema_version":1,"grant_id":"grant-task-7-inspect",
            "issued_by":"human-root","granted_to":"agent","task_scope":["task-7"],"resource_scope":["object:"+self.claim],
            "action_scope":["INSPECT"],"audience_scope":["nexus-inspect"],"issued_at":now.isoformat(),
            "expires_at":(now+timedelta(days=1)).isoformat(),"status":"ACTIVE","policy_version":"1"}, "grant-task-7-inspect")
        self.authority.create_grant({"schema_id":"nexus.delegation_grant","schema_version":1,"grant_id":"grant-task-8-inspect",
            "issued_by":"human-root","granted_to":"agent","task_scope":["task-8"],
            "resource_scope":["object:"+self.claim,"object:"+self.evidence,"approval:approval-7","approval:approval-unresolved"],
            "action_scope":["INSPECT"],"audience_scope":["nexus-inspect"],"issued_at":now.isoformat(),
            "expires_at":(now+timedelta(days=1)).isoformat(),"status":"ACTIVE","policy_version":"1"}, "grant-task-8-inspect")
        self._classify("class-run-8", "RUN", "run-8")
        with self.store._connection() as conn:
            conn.execute("UPDATE tasks SET root_run_id='run-7',status='ACTIVE' WHERE task_id='task-7'")
            conn.execute("INSERT INTO tasks(task_id,requester_id,status,created_at,command_id,root_run_id) VALUES('task-8','human-root','CREATED',?,'task-8',NULL)", (now.isoformat(),))
            conn.execute("INSERT INTO runs(run_id,task_id,subtask_id,parent_run_id,executor_kind,status,grant_id,manifest_ref,budget_reservation_ref,data_boundary_json,classification_assertion_ref,created_at) VALUES('run-8','task-8',NULL,NULL,'ORCHESTRATOR','CREATED','grant-task-8-inspect',NULL,NULL,?,?,?)", (json.dumps({"allowed_classifications":["PUBLIC"],"handling_tags":[]}),"class-run-8",now.isoformat()))
            conn.execute("UPDATE tasks SET root_run_id='run-8',status='ACTIVE' WHERE task_id='task-8'")
            conn.execute("INSERT INTO approval_decisions(approval_id,approver_principal_id,target_type,target_ref,effect_id,payload_integrity_hash,decision,approved_scope_json,policy_version,issued_at,expires_at,reason,request_ref) VALUES('approval-unresolved','human-root','UNMAPPED_ACTION','not-an-object',NULL,NULL,'APPROVE','[]','1',?,NULL,NULL,NULL)", (now.isoformat(),))
        inspector = InspectService(self.store, self.authority)
        with self.assertRaisesRegex(RuntimeDenied, "INSPECT_TASK_SCOPE_MISMATCH"):
            inspector.object_metadata(grant_id="grant-task-8-inspect", task_id="task-8", object_id=self.claim)
        with self.assertRaisesRegex(RuntimeDenied, "INSPECT_TASK_SCOPE_MISMATCH"):
            inspector.object_metadata(grant_id="grant-task-8-inspect", task_id="task-8", object_id=self.evidence)
        with self.assertRaisesRegex(RuntimeDenied, "INSPECT_TASK_SCOPE_MISMATCH"):
            inspector.approval(grant_id="grant-task-8-inspect", task_id="task-8", approval_id="approval-7")
        with self.assertRaisesRegex(RuntimeDenied, "INSPECT_APPROVAL_TASK_UNRESOLVED"):
            inspector.approval(grant_id="grant-task-8-inspect", task_id="task-8", approval_id="approval-unresolved")
        self.assertEqual(inspector.object_metadata(grant_id="grant-task-7-inspect", task_id="task-7", object_id=self.claim)["payload_state"], "PURGED")

    def test_inspect_denies_unbound_and_conflicting_purge_task_provenance(self):
        for task_id in ("task-7", "task-8"):
            now = datetime.now(timezone.utc).isoformat()
            if task_id == "task-8":
                self.authority.create_grant({"schema_id":"nexus.delegation_grant","schema_version":1,"grant_id":"grant-task-8-owner",
                    "issued_by":"human-root","granted_to":"agent","task_scope":["task-8"],"resource_scope":["object:"+self.claim],
                    "action_scope":["INSPECT"],"audience_scope":["nexus-inspect"],"issued_at":now,
                    "expires_at":(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),"status":"ACTIVE","policy_version":"1"}, "grant-task-8-owner")
                self._classify("class-run-8-owner", "RUN", "run-8-owner")
                with self.store._connection() as conn:
                    conn.execute("INSERT INTO tasks(task_id,requester_id,status,created_at,command_id,root_run_id) VALUES('task-8','human-root','CREATED',?,'task-8',NULL)", (now,))
                    conn.execute("INSERT INTO runs(run_id,task_id,subtask_id,parent_run_id,executor_kind,status,grant_id,manifest_ref,budget_reservation_ref,data_boundary_json,classification_assertion_ref,created_at) VALUES('run-8-owner','task-8',NULL,NULL,'ORCHESTRATOR','CREATED','grant-task-8-owner',NULL,NULL,?,?,?)", (json.dumps({"allowed_classifications":["PUBLIC"],"handling_tags":[]}),"class-run-8-owner",now))
                    conn.execute("UPDATE tasks SET root_run_id='run-8-owner',status='ACTIVE' WHERE task_id='task-8'")
        with self.store._connection() as conn:
            for suffix, bound_task in (("conflict", "task-8"), ("legacy", None)):
                plan_id = "inspect-provenance-" + suffix
                command_id = "inspect-provenance-command-" + suffix
                plan_doc = {"schema_id":"nexus.purge_plan","schema_version":1,"plan_id":plan_id,"target_refs":[self.claim],"descendant_refs":[],"affected_indexes":[],"planned_actions":[],"lineage_revision":0,"created_at":"2026-09-25T00:00:00Z","policy_version":"1","plan_hash":"a"*64}
                self.store._record_command(conn, command_id, "create_purge_plan", "b"*64, {"plan_id":plan_id})
                conn.execute("INSERT INTO purge_plan_records(plan_id,plan_hash,lineage_revision,plan_json,command_id,created_at,task_id) VALUES(?,?,?,?,?,?,?)", (plan_id,"a"*64,0,json.dumps(plan_doc),command_id,plan_doc["created_at"],bound_task))
                barrier_id = "inspect-provenance-barrier-" + suffix
                conn.execute("INSERT INTO purge_barriers(barrier_id,plan_id,lineage_revision,status,created_at) VALUES(?,?,0,'RELEASED',?)", (barrier_id,plan_id,plan_doc["created_at"]))
                record_id = "inspect-provenance-record-" + suffix
                conn.execute("INSERT INTO purge_execution_records(record_id,plan_id,barrier_id,status,unresolved_json,record_json,started_at,completed_at) VALUES(?,?,?,'COMPLETED','[]','{}',?,?)", (record_id,plan_id,barrier_id,plan_doc["created_at"],plan_doc["created_at"]))
                conn.execute("INSERT INTO purge_execution_refs(record_id,object_id,payload_uri,integrity_hash) VALUES(?,?,NULL,NULL)", (record_id,self.claim))
                if suffix == "conflict":
                    with self.assertRaisesRegex(RuntimeDenied, "INSPECT_OBJECT_TASK_UNRESOLVED"):
                        InspectService._object_task_owner(conn, self.claim, "task-7", purged=False)
        with self.store._connection() as conn:
            with self.assertRaisesRegex(RuntimeDenied, "INSPECT_OBJECT_TASK_UNRESOLVED"):
                InspectService._object_task_owner(conn, self.claim, "task-7", purged=False)

    def test_purge_execution_cannot_cross_task_boundary(self):
        now = datetime.now(timezone.utc)
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO tasks(task_id,requester_id,status,created_at,command_id,root_run_id) "
                "VALUES('task-8','human-root','CREATED',?,'task-8',NULL)",
                (now.isoformat(),),
            )
        with self.assertRaisesRegex(RuntimeDenied, "PURGE_PLAN_TASK_MISMATCH"):
            self.purge.plan(
                command_id="purge-plan-wrong-task",
                plan_id="plan-wrong-task",
                task_id="task-8",
                target_refs=[self.claim],
            )
        plan = self.purge.plan(
            command_id="purge-plan-cross-task",
            plan_id="plan-cross-task",
            task_id="task-7",
            target_refs=[self.claim],
        )
        with self.store._connection() as conn:
            persisted = conn.execute(
                "SELECT task_id,plan_json FROM purge_plan_records WHERE plan_id='plan-cross-task'"
            ).fetchone()
            self.assertEqual(persisted["task_id"], "task-7")
            self.assertEqual(json.loads(persisted["plan_json"])["task_id"], "task-7")
        self.authority.create_grant(
            {
                "schema_id": "nexus.delegation_grant",
                "schema_version": 1,
                "grant_id": "grant-task-8",
                "issued_by": "human-root",
                "granted_to": "agent",
                "task_scope": ["task-8"],
                "resource_scope": ["plan-cross-task", "record-cross-task"],
                "action_scope": ["PURGE_EXECUTE"],
                "audience_scope": ["nexus-runtime"],
                "issued_at": now.isoformat(),
                "expires_at": (now + timedelta(days=1)).isoformat(),
                "status": "ACTIVE",
                "policy_version": "1",
            },
            "grant-task-8",
        )
        approval_now = datetime.now(timezone.utc).isoformat()
        self.authority.create_approval(
            {
                "schema_id": "nexus.approval_decision",
                "schema_version": 1,
                "approval_id": "approval-cross-task",
                "approver_principal_id": "human-root",
                "target_type": "PURGE_EXECUTE",
                "target_ref": "plan-cross-task",
                "effect_id": "record-cross-task",
                "payload_integrity_hash": plan["plan_hash"],
                "decision": "APPROVE",
                "approved_scope": ["PURGE_EXECUTE", "plan-cross-task"],
                "policy_version": "1",
                "issued_at": approval_now,
            },
            "approval-cross-task",
        )
        with self.store._connection() as conn:
            conn.execute("UPDATE runs SET status='SUCCEEDED' WHERE run_id='run-7'")

        with self.assertRaisesRegex(RuntimeDenied, "PURGE_PLAN_TASK_MISMATCH"):
            self.purge.execute(
                command_id="purge-execute-cross-task",
                record_id="record-cross-task",
                barrier_id="barrier-cross-task",
                plan=plan,
                grant_id="grant-task-8",
                task_id="task-8",
                approval_id="approval-cross-task",
            )

        self.assertEqual(self.store.get_payload(self.claim), b"The synthetic Nexus fact is governed memory.")
        with self.store._connection() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM purge_plan_records WHERE plan_id='plan-wrong-task'").fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM purge_execution_records").fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM purge_barriers").fetchone()[0],
                0,
            )

    def test_purge_backup_restore_replays_external_ledger_without_resurrection(self):
        self.memory.retain_raw(command_id="retain-for-purge", object_id=self.claim, run_id="run-7")
        result = self._human_verification("verify-for-purge")
        self.memory.create_candidate(command_id="candidate-for-purge", candidate_id="candidate-7", claim_ref=self.claim, evidence_refs=[self.evidence], owner="agent", classification_assertion_ref="class-claim-7", verification_ref=result["verification_id"], review_trigger="review")
        approval_id, effect_id, inspector = self._seed_purge_identifier_audit()
        plan = self.purge.plan(command_id="purge-plan-success", plan_id="plan-7", task_id="task-7", target_refs=[self.claim])
        self._approve_purge(plan["plan_hash"])
        backup_root = self.root / "old-backup"
        (backup_root / "objects").mkdir(parents=True)
        destination = sqlite3.connect(backup_root / "nexus.sqlite")
        try:
            with self.store._connection() as source:
                source.backup(destination)
        finally:
            destination.close()
        shutil.copytree(self.data_root / "objects", backup_root / "objects", dirs_exist_ok=True)
        with self.store._connection() as conn:
            conn.execute("UPDATE runs SET status='SUCCEEDED' WHERE run_id='run-7'")
        purged_payload_hash = hashlib.sha256(self.store.get_payload(self.claim)).hexdigest()
        outcome = self.purge.execute(command_id="purge-execute-success", record_id="record-7", barrier_id="barrier-7", plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7")
        self.assertEqual(outcome["status"], "COMPLETED")
        with self.assertRaises(PurgedObject):
            self.store.get_payload(self.claim)
        # The existing immutable RunManifest derived from this input is part
        # of the purge closure; retaining its index row must not retain bytes.
        with self.assertRaises(PurgedObject):
            self.store.get_payload("manifest-7")
        self.assertEqual(self.store.get_object_metadata("manifest-7"), {"object_id": "manifest-7", "payload_state": "PURGED"})
        tombstone = self.store.get_object_metadata(self.claim)
        self.assertEqual(tombstone, {"object_id": self.claim, "payload_state": "PURGED"})
        self.assertNotIn("integrity_hash", tombstone)
        effect_projection = inspector.effect(grant_id="purge-audit-inspect", task_id="task-7", effect_id=effect_id)
        approval_projection = inspector.approval(grant_id="purge-audit-inspect", task_id="task-7", approval_id=approval_id, include_payload_hash=True)
        trace_projection = inspector.trace_events(grant_id="purge-audit-inspect", task_id="task-7", run_id="run-7")
        self.assertEqual(effect_projection["target_ref"], "REDACTED_PURGED")
        self.assertEqual(approval_projection["target_ref"], "REDACTED_PURGED")
        self.assertEqual(approval_projection["effect_id"], "REDACTED_PURGED")
        self.assertEqual(approval_projection["payload_integrity_hash"], "REDACTED_PURGED")
        self.assertEqual(trace_projection[-1]["object_refs"], ["REDACTED_PURGED"])
        self.assertEqual(trace_projection[-1]["effect_refs"], ["REDACTED_PURGED"])
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM raw_history_fts WHERE raw_history_fts MATCH 'governed' ").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM admitted_memory_fts WHERE admitted_memory_fts MATCH 'governed' ").fetchone()[0], 0)
            # Purge erases payload-derived identifiers but preserves immutable
            # identity, decision and outcome facts.
            retained_effect = conn.execute("SELECT effect_outcome,payload_integrity_hash,target_ref FROM effects WHERE effect_id=?", (effect_id,)).fetchone()
            self.assertEqual((retained_effect["effect_outcome"], retained_effect["payload_integrity_hash"], retained_effect["target_ref"]), ("NOT_COMMITTED", "0" * 64, "REDACTED_PURGED"))
            approval_fact = conn.execute("SELECT decision,approver_principal_id,target_ref,payload_integrity_hash,approved_scope_json FROM approval_decisions WHERE approval_id=?", (approval_id,)).fetchone()
            self.assertEqual((approval_fact["decision"], approval_fact["approver_principal_id"], approval_fact["target_ref"], approval_fact["payload_integrity_hash"], approval_fact["approved_scope_json"]), ("APPROVE", "human-root", "REDACTED_PURGED", None, "[]"))
            trace_fact = conn.execute("SELECT event_json FROM trace_events WHERE event_id='evt-purge-ref-event'").fetchone()[0]
            self.assertNotIn("https://private.example/recipient/42", trace_fact)
            self.assertNotIn(purged_payload_hash, trace_fact)
            self.assertEqual(json.loads(trace_fact)["typed_metadata"], {
                "execution_state": "CANCELLED",
                "effect_outcome": "NOT_COMMITTED",
                "reconciliation_status": "NOT_REQUIRED",
            })
            self.assertNotIn(effect_id, trace_fact)
            self.assertTrue(conn.execute("SELECT 1 FROM json_each(?, '$.object_refs') WHERE value='REDACTED_PURGED'", (trace_fact,)).fetchone())
            purge_ref = conn.execute("SELECT payload_uri,integrity_hash FROM purge_execution_refs WHERE record_id='record-7' AND object_id=?", (self.claim,)).fetchone()
            self.assertEqual((purge_ref["payload_uri"], purge_ref["integrity_hash"]), (None, None))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM run_manifest_inputs WHERE input_object_id IN (?,?) OR manifest_object_id IN (?,?)", (self.claim,"manifest-7",self.claim,"manifest-7")).fetchone()[0], 0)
            self.assertNotIn("https://private.example/recipient/42", json.dumps([tuple(row) for row in conn.execute("SELECT target_ref,payload_integrity_hash FROM effects UNION ALL SELECT target_ref,payload_integrity_hash FROM approval_decisions")]))
            self.assertNotIn(purged_payload_hash, json.dumps([tuple(row) for row in conn.execute("SELECT target_ref,payload_integrity_hash FROM effects UNION ALL SELECT target_ref,payload_integrity_hash FROM approval_decisions")]))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE runs SET status='READY' WHERE run_id='run-pending'")
        restored = ObjectStore(backup_root)
        try:
            restored_memory = MemoryService(restored, self.authority, VerificationService(restored, self.authority))
            restored_purge = PurgeService(restored, self.authority, restored_memory, independent_journal_path=self.journal_path)
            replay = restored_purge.replay_independent_journal()
            self.assertTrue(replay["normal_allowed"])
            self.assertEqual(replay["purged_refs_checked"], 3)
            with self.assertRaises(PurgedObject):
                restored.get_payload(self.claim)
            with self.assertRaises(PurgedObject):
                restored.get_payload("manifest-7")
            self.assertEqual(restored_memory.search_raw(query="governed memory", run_id="run-7"), [])
            self.assertEqual(restored_memory.search_admitted(query="governed memory", run_id="run-7"), [])
            restored_inspector = InspectService(restored, self.authority)
            self.assertEqual(restored_inspector.effect(grant_id="purge-audit-inspect", task_id="task-7", effect_id=effect_id)["target_ref"], "REDACTED_PURGED")
            restored_approval = restored_inspector.approval(grant_id="purge-audit-inspect", task_id="task-7", approval_id=approval_id, include_payload_hash=True)
            self.assertEqual(restored_approval["payload_integrity_hash"], "REDACTED_PURGED")
            restored_trace = restored_inspector.trace_events(grant_id="purge-audit-inspect", task_id="task-7", run_id="run-7")
            self.assertEqual(restored_trace[-1]["object_refs"], ["REDACTED_PURGED"])
            with restored._connection() as conn:
                self.assertEqual(conn.execute("SELECT status FROM purge_barriers WHERE barrier_id='barrier-7'").fetchone()[0], "RELEASED")
                self.assertEqual([row[0] for row in conn.execute("SELECT action FROM purge_ledger ORDER BY ledger_seq")], ["BARRIER_INSTALLED", "BARRIER_RELEASED"])
                raw_effect = conn.execute("SELECT effect_outcome,target_ref,payload_integrity_hash FROM effects WHERE effect_id=?", (effect_id,)).fetchone()
                self.assertEqual(tuple(raw_effect), ("NOT_COMMITTED", "REDACTED_PURGED", "0" * 64))
                self.assertEqual(tuple(conn.execute("SELECT target_ref,payload_integrity_hash FROM approval_decisions WHERE approval_id=?", (approval_id,)).fetchone()), ("REDACTED_PURGED", None))
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM run_manifest_inputs WHERE input_object_id IN (?,?) OR manifest_object_id IN (?,?)", (self.claim,"manifest-7",self.claim,"manifest-7")).fetchone()[0], 0)
        finally:
            restored.close()

    def _seed_purge_identifier_audit(self):
        target = "https://private.example/recipient/42"
        digest = hashlib.sha256(self.store.get_payload(self.claim)).hexdigest()
        budgets = BudgetService(self.store)
        budgets.create_account(command_id="purge-audit-budget-create", account_id="purge-audit-budget", task_id="task-7", amount_limit=0, unit="test", model_call_limit=0, tool_call_limit=0, child_run_limit=0)
        reservation = budgets.reserve(command_id="purge-audit-reserve", account_id="purge-audit-budget", task_id="task-7", run_id="run-7", amount=0)
        approval_id = "approval-purge-sensitive"
        effect_id = "effect-purge-sensitive"
        now = datetime.now(timezone.utc).isoformat()
        self.authority.create_approval({"schema_id":"nexus.approval_decision","schema_version":1,"approval_id":approval_id,
            "approver_principal_id":"human-root","target_type":"FAKE_ACTION","target_ref":target,"effect_id":effect_id,
            "payload_integrity_hash":digest,"decision":"APPROVE","approved_scope":["FAKE_ACTION",target],"policy_version":"1","issued_at":now}, "create-approval-purge-sensitive")
        effect = {"effect_id":effect_id,"run_id":"run-7","idempotency_key":"purge-sensitive-key","execution_state":"CANCELLED","effect_outcome":"NOT_COMMITTED","reconciliation_status":"NOT_REQUIRED"}
        with self.store._connection() as conn:
            conn.execute("INSERT INTO effects(effect_id,run_id,tool_id,tool_descriptor_version,action_type,target_ref,payload_integrity_hash,payload_object_ref,idempotency_key,grant_id,approval_ref,budget_reservation_ref,execution_state,effect_outcome,reconciliation_status,external_receipt_ref,effect_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (effect_id,"run-7","synthetic-tool","1","FAKE_ACTION",target,digest,self.claim,"purge-sensitive-key","grant-7",approval_id,reservation,"CANCELLED","NOT_COMMITTED","NOT_REQUIRED",None,json.dumps(effect,sort_keys=True),now,now))
            conn.execute("UPDATE tasks SET root_run_id='run-7',status='ACTIVE' WHERE task_id='task-7'")
        self._classify("class-purge-ref-event","TRACE_EVENT","evt-purge-ref-event")
        # Seed a schema-valid immutable fact as if emitted by EffectService;
        # ordinary callers cannot append this reserved Kernel event type.
        event = {"schema_id":"nexus.trace_event","schema_version":1,"event_id":"evt-purge-ref-event",
            "run_id":"run-7","seq_no":1,"event_type":"nexus.effect.outcome_recorded","occurred_at":now,
            "actor_id":"agent","object_refs":[self.claim],"effect_refs":[effect_id],"policy_refs":[],"authority_refs":[],
            "data_boundary":{"allowed_classifications":["PUBLIC"],"handling_tags":[]},
            "classification_assertion_ref":"class-purge-ref-event",
            "typed_metadata":{"effect_id":effect_id,"execution_state":"CANCELLED","effect_outcome":"NOT_COMMITTED","reconciliation_status":"NOT_REQUIRED"}}
        with self.store._connection() as conn:
            conn.execute("INSERT INTO trace_events(event_id,run_id,seq_no,event_type,occurred_at,actor_id,event_json) VALUES(?,?,?,?,?,?,?)",
                (event["event_id"],event["run_id"],event["seq_no"],event["event_type"],event["occurred_at"],event["actor_id"],json.dumps(event,sort_keys=True)))
        now_dt = datetime.now(timezone.utc)
        self.authority.create_grant({"schema_id":"nexus.delegation_grant","schema_version":1,"grant_id":"purge-audit-inspect",
            "issued_by":"human-root","granted_to":"agent","task_scope":["task-7"],
            "resource_scope":["task:task-7","effect:"+effect_id,"approval:"+approval_id,"approval:"+approval_id+":payload-integrity","trace:run-7"],
            "action_scope":["INSPECT","INSPECT_PROTECTED"],"audience_scope":["nexus-inspect"],"issued_at":now_dt.isoformat(),
            "expires_at":(now_dt+timedelta(days=1)).isoformat(),"status":"ACTIVE","policy_version":"1"}, "grant-purge-audit-inspect")
        return approval_id,effect_id,InspectService(self.store,self.authority)

    def test_old_snapshot_recovery_replays_purge_ledger_before_normal(self):
        self.memory.retain_raw(command_id="retain-for-recovery", object_id=self.claim, run_id="run-7")
        result = self._human_verification("verify-for-recovery")
        self.memory.create_candidate(command_id="candidate-for-recovery", candidate_id="candidate-7", claim_ref=self.claim, evidence_refs=[self.evidence], owner="agent", classification_assertion_ref="class-claim-7", verification_ref=result["verification_id"], review_trigger="review")
        plan = self.purge.plan(command_id="recovery-purge-plan", plan_id="plan-7", task_id="task-7", target_refs=[self.claim])
        self._approve_purge(plan["plan_hash"])

        snapshot_root = self.root / "recovery-snapshot"
        (snapshot_root / "objects").mkdir(parents=True)
        destination = sqlite3.connect(snapshot_root / "nexus.sqlite")
        try:
            with self.store._connection() as source:
                source.backup(destination)
        finally:
            destination.close()
        shutil.copytree(self.data_root / "objects", snapshot_root / "objects", dirs_exist_ok=True)

        # Enter RECOVERY on the isolated snapshot using the ordinary authorized Runtime mode path.
        snapshot_store = ObjectStore(snapshot_root, policy=self.authority.policy)
        try:
            snapshot_authority = AuthorityService(snapshot_store, self.authority.policy)
            now = datetime.now(timezone.utc)
            snapshot_authority.create_grant({"schema_id":"nexus.delegation_grant","schema_version":1,"grant_id":"recovery-mode-grant","issued_by":"human-root","granted_to":"agent","task_scope":["recovery-mode-task"],"resource_scope":["runtime-mode:instance","recovery-mode-root"],"action_scope":["RUNTIME_CONFIGURE","RUN_CREATE","TRACE_APPEND"],"audience_scope":["nexus-runtime"],"issued_at":now.isoformat(),"expires_at":(now+timedelta(days=1)).isoformat(),"status":"ACTIVE","policy_version":"1"}, "recovery-mode-grant-create")
            trace = TraceRuntime(snapshot_store, snapshot_authority)
            trace.create_task({"schema_id":"nexus.task","schema_version":1,"task_id":"recovery-mode-task","requester_id":"human-root","status":"CREATED","created_at":now.isoformat(),"command_id":"recovery-mode-task-create"})
            with snapshot_store._connection() as conn:
                conn.execute("INSERT INTO classification_assertions VALUES(?,?,?,?,?,?,?, ?,NULL)", ("recovery-mode-run-class", "RUN", "recovery-mode-root", "PUBLIC", "[]", "1", "authorized isolated recovery test", "agent"))
                conn.execute("INSERT INTO classification_assertions VALUES(?,?,?,?,?,?,?, ?,NULL)", ("recovery-mode-root-event-class", "TRACE_EVENT", "evt-recovery-mode-root-create", "PUBLIC", "[]", "1", "authorized isolated recovery test", "agent"))
                conn.execute("INSERT INTO classification_assertions VALUES(?,?,?,?,?,?,?, ?,NULL)", ("recovery-mode-event-class", "TRACE_EVENT", "evt-enter-recovery", "PUBLIC", "[]", "1", "authorized isolated recovery test", "agent"))
            trace.create_run({"schema_id":"nexus.run","schema_version":1,"run_id":"recovery-mode-root","task_id":"recovery-mode-task","executor_kind":"ORCHESTRATOR","status":"CREATED","grant_id":"recovery-mode-grant","data_boundary":{"allowed_classifications":["PUBLIC"],"handling_tags":[]},"classification_assertion_ref":"recovery-mode-run-class","created_at":now.isoformat()}, command_id="recovery-mode-root-create", event_classification_assertion_ref="recovery-mode-root-event-class")
            modes = RuntimeModeService(snapshot_store, snapshot_authority)
            snapshot_memory = MemoryService(snapshot_store, snapshot_authority, VerificationService(snapshot_store, snapshot_authority))
            for target_mode in ("SAFE", "STATELESS", "NORMAL"):
                command = "enter-" + target_mode.lower()
                assertion = "class-" + command
                with snapshot_store._connection() as conn:
                    conn.execute("INSERT INTO classification_assertions VALUES(?,?,?,?,?,?,?, ?,NULL)", (assertion, "TRACE_EVENT", "evt-" + command, "PUBLIC", "[]", "1", "authorized isolated recovery mode test", "agent"))
                result = modes.set_mode(command_id=command, grant_id="recovery-mode-grant", task_id="recovery-mode-task", mode=target_mode, classification_assertion_ref=assertion)
                self.assertEqual(result["mode"], target_mode)
                if target_mode == "STATELESS":
                    with self.assertRaisesRegex(RuntimeDenied, "RUNTIME_MODE_DENIED"):
                        snapshot_memory.search_raw(query="governed memory", run_id="run-7")
            snapshot_store.close()
            snapshot_store = ObjectStore(snapshot_root, policy=self.authority.policy)
            snapshot_authority = AuthorityService(snapshot_store, self.authority.policy)
            modes = RuntimeModeService(snapshot_store, snapshot_authority)
            self.assertEqual(modes.current()["mode"], "NORMAL")
            with snapshot_store._connection() as conn:
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            modes.set_mode(command_id="enter-recovery", grant_id="recovery-mode-grant", task_id="recovery-mode-task", mode="RECOVERY", classification_assertion_ref="recovery-mode-event-class")
            self.assertEqual(modes.current()["mode"], "RECOVERY")
        finally:
            snapshot_store.close()

        with self.store._connection() as conn:
            conn.execute("UPDATE runs SET status='SUCCEEDED' WHERE run_id='run-7'")
        outcome = self.purge.execute(command_id="recovery-purge-execute", record_id="record-7", barrier_id="barrier-7", plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7")
        self.assertEqual(outcome["status"], "COMPLETED")

        restored_root = self.root / "restored-old-snapshot"
        shutil.copytree(snapshot_root, restored_root)
        restored = ObjectStore(restored_root, policy=self.authority.policy)
        try:
            self.assertEqual(RuntimeModeService(restored, AuthorityService(restored, self.authority.policy)).current()["mode"], "RECOVERY")
            restored_authority = AuthorityService(restored, self.authority.policy)
            restored_memory = MemoryService(restored, restored_authority, VerificationService(restored, restored_authority))
            restored_purge = PurgeService(restored, restored_authority, restored_memory, independent_journal_path=self.journal_path)
            restored_modes = RuntimeModeService(restored, restored_authority)
            with self.assertRaisesRegex(RuntimeDenied, "RUNTIME_RECOVERY_CORE_BYPASS"):
                restored_memory.search_raw(query="governed memory", run_id="run-7")
            report = restored_modes.complete_validated_recovery(command_id="finish-old-snapshot-restore", purge_service=restored_purge)
            self.assertEqual(report["mode"], "NORMAL")
            self.assertTrue(report["purge_report"]["normal_allowed"])
            with self.assertRaises(PurgedObject):
                restored.get_payload(self.claim)
            self.assertEqual(restored_memory.search_raw(query="governed memory", run_id="run-7"), [])
            self.assertEqual(restored_memory.search_admitted(query="governed memory", run_id="run-7"), [])
            with restored._connection() as conn:
                self.assertEqual(conn.execute("SELECT status FROM purge_barriers WHERE barrier_id='barrier-7'").fetchone()[0], "RELEASED")
                self.assertEqual(conn.execute("SELECT payload_state FROM object_states WHERE object_id=?", (self.claim,)).fetchone()[0], "PURGED")
            self.assertEqual(restored_modes.current()["mode"], "NORMAL")
        finally:
            restored.close()

    def _seed_unknown_effect(self):
        now = datetime.now(timezone.utc).isoformat()
        with self.store._connection() as conn:
            conn.execute("INSERT INTO budget_accounts(account_id,task_id,amount_limit,unit,model_call_limit,tool_call_limit,child_run_limit) VALUES('budget-7','task-7',10,'credits',0,1,0)")
            conn.execute("INSERT INTO budget_reservations(reservation_id,account_id,run_id,amount,model_calls,tool_calls,child_runs,state,command_id,created_at) VALUES('reservation-7','budget-7','run-7',1,0,1,0,'RESERVED','reservation-7',?)", (now,))
            effect = {"effect_id": "effect-unknown-7", "run_id": "run-7", "idempotency_key": "key-unknown-7", "execution_state": "COMMITTING", "effect_outcome": "UNKNOWN", "reconciliation_status": "PENDING"}
            conn.execute("INSERT INTO effects(effect_id,run_id,tool_id,tool_descriptor_version,action_type,target_ref,payload_integrity_hash,payload_object_ref,idempotency_key,grant_id,approval_ref,budget_reservation_ref,execution_state,effect_outcome,reconciliation_status,external_receipt_ref,effect_json,created_at,updated_at) VALUES('effect-unknown-7','run-7','fake','1','FAKE_WRITE','target','" + "0" * 64 + "','claim-7','key-unknown-7','grant-7',NULL,'reservation-7','COMMITTING','UNKNOWN','PENDING',NULL,?,?,?)", (json.dumps(effect, sort_keys=True), now, now))


if __name__ == "__main__":
    unittest.main()
