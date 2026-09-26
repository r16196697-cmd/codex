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
        self.journal_path = self.root / "independent" / "purge.jsonl"
        self.store = ObjectStore(self.data_root, independent_purge_journal_path=self.journal_path)
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

    def _classify(self, assertion_id, subject_type, subject_ref, level="PUBLIC", tags=()):
        with self.store._connection() as conn:
            conn.execute("INSERT INTO classification_assertions(assertion_id,subject_type,subject_ref,sensitivity_level,handling_tags_json,policy_version,reason,actor_id) VALUES(?,?,?,?,?,'1','synthetic Step 7 fixture','agent')", (assertion_id, subject_type, subject_ref, level, json.dumps(list(tags))))

    def _put(self, object_id, payload):
        class_ref = "class-" + object_id
        self._classify(class_ref, "OBJECT", object_id)
        return self.store.put_object(command_id="put-" + object_id, object_id=object_id, payload=payload, object_type="artifact", created_by_run="run-7", classification_assertion_ref=class_ref)

    def _approve_purge(self, plan_hash, *, expires_at=None):
        now = datetime.now(timezone.utc).isoformat()
        approval = {"schema_id": "nexus.approval_decision", "schema_version": 1, "approval_id": "approval-7", "approver_principal_id": "human-root", "target_type": "PURGE_EXECUTE", "target_ref": "plan-7", "effect_id": "record-7", "payload_integrity_hash": plan_hash, "decision": "APPROVE", "approved_scope": ["PURGE_EXECUTE", "plan-7"], "policy_version": "1", "issued_at": now}
        if expires_at is not None:
            approval["expires_at"] = expires_at
        self.authority.create_approval(approval, "approval-7")

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
        with self.assertRaises(PurgeBarrierActive):
            self.store.get_payload(self.claim)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM purge_ledger").fetchone()[0], 2)
        replay = self.purge.replay_independent_journal()
        self.assertFalse(replay["normal_allowed"])
        self.assertEqual(replay["held_refs"], 3)
        with self.assertRaises(PurgeBarrierActive):
            self.store.get_payload(self.claim)

    def test_crash_after_independent_barrier_record_restores_conservative_purge_hold(self):
        self.memory.retain_raw(command_id="retain-before-journal-crash", object_id=self.claim, run_id="run-7")
        plan = self.purge.plan(command_id="journal-crash-plan-command", plan_id="plan-7", task_id="task-7", target_refs=[self.claim])
        self._approve_purge(plan["plan_hash"])
        original_append = self.purge.journal.append
        def fail_install(**kwargs):
            if kwargs.get("action") == "BARRIER_INSTALLED":
                raise OSError("SIMULATED_JOURNAL_INSTALL_FAILURE")
            return original_append(**kwargs)
        with mock.patch.object(self.purge.journal, "append", side_effect=fail_install):
            first = self.purge.execute(command_id="journal-crash-execute", record_id="record-7", barrier_id="barrier-7", plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7")
        self.assertEqual(first["status"], "PARTIAL")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM purge_barriers WHERE barrier_id='barrier-7'").fetchone()[0], "PARTIAL")
        replay = self.purge.replay_independent_journal()
        self.assertFalse(replay["normal_allowed"])
        self.assertEqual(replay["held_refs"], 3)
        with self.assertRaises(PurgeBarrierActive):
            self.store.get_payload(self.claim)
        self.store.close()
        self.store = ObjectStore(self.data_root, independent_purge_journal_path=self.journal_path)
        self.addCleanup(self.store.close)
        self.authority = AuthorityService(self.store, self.authority.policy)
        self.memory = MemoryService(self.store, self.authority, VerificationService(self.store, self.authority))
        self.purge = PurgeService(self.store, self.authority, self.memory, independent_journal_path=self.journal_path)
        recovered = self.purge.replay_independent_journal()
        self.assertFalse(recovered["normal_allowed"])
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM purge_barriers WHERE barrier_id='barrier-7'").fetchone()[0], "PARTIAL")

    def test_process_loss_after_local_barrier_before_journal_keeps_reads_and_writes_held_then_resumes(self):
        class SimulatedProcessLoss(BaseException):
            pass

        with self.store._connection() as conn:
            conn.execute("UPDATE runs SET status='SUCCEEDED' WHERE run_id='run-7'")
        plan = self.purge.plan(command_id="barrier-before-journal-plan", plan_id="plan-7", task_id="task-7", target_refs=[self.claim])
        self._approve_purge(plan["plan_hash"])
        with mock.patch.object(self.purge.journal, "append", side_effect=SimulatedProcessLoss("after local barrier commit")):
            with self.assertRaises(SimulatedProcessLoss):
                self.purge.execute(command_id="barrier-before-journal-execute", record_id="record-7", barrier_id="barrier-7", plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM purge_barriers WHERE barrier_id='barrier-7'").fetchone()[0], "ACTIVE")
            self.assertEqual(conn.execute("SELECT status FROM purge_execution_records WHERE record_id='record-7'").fetchone()[0], "RUNNING")
        self.assertEqual(self.purge.journal.read(), [])
        with self.assertRaises(PurgeBarrierActive):
            self.store.get_payload(self.claim)
        with self.assertRaises(PurgeBarrierActive):
            self.store.add_relation(command_id="barrier-before-journal-write", from_id=self.claim, relation_type="supports", to_id="evidence-7")

        self.store.close()
        self.store = ObjectStore(self.data_root, independent_purge_journal_path=self.journal_path)
        self.addCleanup(self.store.close)
        self.authority = AuthorityService(self.store, self.authority.policy)
        self.verifier = VerificationService(self.store, self.authority)
        self.memory = MemoryService(self.store, self.authority, self.verifier)
        self.purge = PurgeService(self.store, self.authority, self.memory, independent_journal_path=self.journal_path)
        with self.assertRaises(PurgeBarrierActive):
            self.store.get_object_metadata(self.claim)
        completed = self.purge.execute(command_id="barrier-before-journal-execute", record_id="record-7", barrier_id="barrier-7", plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7")
        self.assertEqual(completed["status"], "COMPLETED")
        self.assertEqual(sum(row["action"] == "BARRIER_INSTALLED" for row in self.purge.journal.read()), 1)

    def test_process_loss_after_durable_install_journal_before_payload_delete_resumes_fail_closed(self):
        class SimulatedProcessLoss(BaseException):
            pass

        with self.store._connection() as conn:
            conn.execute("UPDATE runs SET status='SUCCEEDED' WHERE run_id='run-7'")
        plan = self.purge.plan(command_id="journal-before-delete-plan", plan_id="plan-7", task_id="task-7", target_refs=[self.claim])
        self._approve_purge(plan["plan_hash"])
        with mock.patch.object(self.purge, "_purge_payloads_and_indexes", side_effect=SimulatedProcessLoss("after durable install journal")):
            with self.assertRaises(SimulatedProcessLoss):
                self.purge.execute(command_id="journal-before-delete-execute", record_id="record-7", barrier_id="barrier-7", plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7")
        self.assertEqual(self.purge.journal.read()[0]["action"], "BARRIER_INSTALLED")
        with self.assertRaises(PurgeBarrierActive):
            self.store.get_payload(self.claim)
        with self.assertRaises(PurgeBarrierActive):
            self.store.add_relation(command_id="journal-before-delete-write", from_id=self.claim, relation_type="supports", to_id="evidence-7")

        self.store.close()
        self.store = ObjectStore(self.data_root, independent_purge_journal_path=self.journal_path)
        self.addCleanup(self.store.close)
        self.authority = AuthorityService(self.store, self.authority.policy)
        self.verifier = VerificationService(self.store, self.authority)
        self.memory = MemoryService(self.store, self.authority, self.verifier)
        self.purge = PurgeService(self.store, self.authority, self.memory, independent_journal_path=self.journal_path)
        self.assertFalse(self.purge.replay_independent_journal()["normal_allowed"])
        completed = self.purge.execute(command_id="journal-before-delete-execute", record_id="record-7", barrier_id="barrier-7", plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7")
        self.assertEqual(completed["status"], "COMPLETED")
        with self.assertRaises(PurgedObject):
            self.store.get_payload(self.claim)

    def test_each_governed_redaction_failure_rolls_back_the_entire_purge_projection(self):
        self.memory.retain_raw(command_id="atomic-redact-retain", object_id=self.claim, run_id="run-7")
        verification = self.verifier.verify_object_integrity(verification_id="atomic-redact-verification", target_ref=self.claim, evidence_refs=[self.evidence], run_id="run-7")
        self.memory.create_candidate(command_id="atomic-redact-candidate", candidate_id="candidate-7", claim_ref=self.claim, evidence_refs=[self.evidence], owner="agent", classification_assertion_ref="class-claim-7", verification_ref=verification["verification_id"], review_trigger="atomic purge redaction")
        self._human_verification("atomic-redact-human")
        self._seed_purge_identifier_audit()
        with self.store._connection() as conn:
            conn.execute("UPDATE runs SET status='SUCCEEDED' WHERE run_id='run-7'")
        plan = self.purge.plan(command_id="atomic-redact-plan", plan_id="plan-7", task_id="task-7", target_refs=[self.claim])
        self._approve_purge(plan["plan_hash"])

        class SimulatedProcessLoss(BaseException):
            pass

        with mock.patch.object(self.purge, "_purge_payloads_and_indexes", side_effect=SimulatedProcessLoss("barrier installed")):
            with self.assertRaises(SimulatedProcessLoss):
                self.purge.execute(command_id="atomic-redact-first", record_id="record-7", barrier_id="barrier-7", plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7")

        failures = [
            ("verification_results", "target_ref", "OLD.target_ref='claim-7'"),
            ("memory_candidates", "claim_ref", "OLD.claim_ref='claim-7'"),
            ("classification_assertions", "subject_ref", "OLD.subject_ref='claim-7'"),
            ("effects", "payload_object_ref", "OLD.payload_object_ref='claim-7'"),
            ("approval_decisions", "target_ref", "OLD.target_ref='claim-7'"),
            ("delegation_grants", "resource_scope_json", "instr(OLD.resource_scope_json, '\"claim-7\"')>0"),
            ("command_ledger", "result_json", "instr(OLD.result_json, '\"claim-7\"')>0"),
        ]
        for index, (table, column, condition) in enumerate(failures):
            trigger = f"inject_atomic_redaction_failure_{index}"
            with self.store._connection() as conn:
                conn.execute(f"CREATE TRIGGER {trigger} BEFORE UPDATE OF {column} ON {table} WHEN {condition} BEGIN SELECT RAISE(ABORT,'SIMULATED_{table}_REDACTION_FAILURE'); END")
            try:
                self.purge._purge_payloads_and_indexes([self.claim])
            except sqlite3.IntegrityError as exc:
                self.assertIn(f"SIMULATED_{table}_REDACTION_FAILURE", str(exc))
            else:
                self.fail(f"fault trigger for {table} was not reached")
            with self.store._connection() as conn:
                conn.execute(f"DROP TRIGGER {trigger}")
                self.assertEqual(conn.execute("SELECT payload_state FROM object_states WHERE object_id=?", (self.claim,)).fetchone()[0], "AVAILABLE")
                self.assertEqual(conn.execute("SELECT target_ref FROM verification_results WHERE verification_id=?", (verification["verification_id"],)).fetchone()[0], self.claim)
                self.assertEqual(conn.execute("SELECT claim_ref FROM memory_candidates WHERE candidate_id='candidate-7'").fetchone()[0], self.claim)
                self.assertEqual(conn.execute("SELECT subject_ref FROM classification_assertions WHERE assertion_id='class-claim-7'").fetchone()[0], self.claim)
            with self.assertRaises(PurgeBarrierActive):
                self.store.get_payload(self.claim)

        completed = self.purge.execute(command_id="atomic-redact-retry", record_id="record-7", barrier_id="barrier-7", plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7")
        self.assertEqual(completed["status"], "COMPLETED")

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
            with self.store._connection() as conn:
                payload_uri = conn.execute("SELECT payload_uri FROM object_envelopes WHERE object_id=?", (object_id,)).fetchone()[0]
            with self.assertRaises(PurgeBarrierActive):
                self.store.get_object_metadata(object_id)
            if not self.store._payload_path(payload_uri).is_file():
                missing_after_rollback.append(object_id)
        self.assertTrue(missing_after_rollback, "the injected SQLite rollback occurs after filesystem unlink")
        self.assertEqual(self.memory.search_raw(query="governed memory", run_id="run-7"), [])

        self.store.close()
        self.store = ObjectStore(self.data_root, independent_purge_journal_path=self.journal_path)
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
            self.store = ObjectStore(self.data_root, independent_purge_journal_path=self.journal_path)
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
        self.store = ObjectStore(self.data_root, independent_purge_journal_path=self.journal_path)
        self.addCleanup(self.store.close)
        self.authority = AuthorityService(self.store, self.authority.policy)
        self.memory = MemoryService(self.store, self.authority, VerificationService(self.store, self.authority))
        self.purge = PurgeService(self.store, self.authority, self.memory, independent_journal_path=self.journal_path)
        if self.store._current_runtime_mode() == "RECOVERY":
            RuntimeModeService(self.store, self.authority).complete_validated_recovery(command_id="finish-release-projection-recovery", purge_service=self.purge)
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
        self.store = ObjectStore(self.data_root, independent_purge_journal_path=self.journal_path)
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
        self.store = ObjectStore(self.data_root, independent_purge_journal_path=self.journal_path)
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
            "issued_by":"human-root","granted_to":"agent","task_scope":["task-7"],"resource_scope":["task:task-7","object:"+self.claim],
            "action_scope":["INSPECT"],"audience_scope":["nexus-inspect"],"issued_at":now.isoformat(),
            "expires_at":(now+timedelta(days=1)).isoformat(),"status":"ACTIVE","policy_version":"1"}, "grant-task-7-inspect")
        self.authority.create_grant({"schema_id":"nexus.delegation_grant","schema_version":1,"grant_id":"grant-task-8-inspect",
            "issued_by":"human-root","granted_to":"agent","task_scope":["task-8"],
            "resource_scope":["task:task-8","object:"+self.claim,"object:"+self.evidence,"approval:approval-7","approval:approval-unresolved"],
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

    def test_classification_lower_approval_inspection_resolves_live_and_purged_object_owner(self):
        now = datetime.now(timezone.utc)
        self.authority.create_grant({
            "schema_id":"nexus.delegation_grant","schema_version":1,"grant_id":"inspect-classification-7",
            "issued_by":"human-root","granted_to":"agent","task_scope":["task-7"],
            "resource_scope":["approval:approval-classification-lower","approval:approval-classification-secret","object:"+self.claim],"action_scope":["INSPECT"],
            "audience_scope":["nexus-inspect"],"issued_at":now.isoformat(),
            "expires_at":(now+timedelta(days=1)).isoformat(),"status":"ACTIVE","policy_version":"1",
        }, "inspect-classification-7")
        self.authority.create_grant({
            "schema_id":"nexus.delegation_grant","schema_version":1,"grant_id":"inspect-classification-8",
            "issued_by":"human-root","granted_to":"agent","task_scope":["task-8"],
            "resource_scope":["approval:approval-classification-lower","object:"+self.claim],"action_scope":["INSPECT"],
            "audience_scope":["nexus-inspect"],"issued_at":now.isoformat(),
            "expires_at":(now+timedelta(days=1)).isoformat(),"status":"ACTIVE","policy_version":"1",
        }, "inspect-classification-8")
        self._classify("class-run-8-approval", "RUN", "run-8-approval")
        with self.store._connection() as conn:
            conn.execute("UPDATE tasks SET root_run_id='run-7',status='ACTIVE' WHERE task_id='task-7'")
            conn.execute("INSERT INTO tasks(task_id,requester_id,status,created_at,command_id,root_run_id) VALUES('task-8','human-root','CREATED',?,'task-8',NULL)", (now.isoformat(),))
            conn.execute("INSERT INTO runs(run_id,task_id,subtask_id,parent_run_id,executor_kind,status,grant_id,manifest_ref,budget_reservation_ref,data_boundary_json,classification_assertion_ref,created_at) VALUES('run-8-approval','task-8',NULL,NULL,'ORCHESTRATOR','CREATED','inspect-classification-8',NULL,NULL,?,?,?)", (json.dumps({"allowed_classifications":["PUBLIC"],"handling_tags":[]}),"class-run-8-approval",now.isoformat()))
            conn.execute("UPDATE tasks SET root_run_id='run-8-approval',status='ACTIVE' WHERE task_id='task-8'")
        self.authority.create_approval({
            "schema_id":"nexus.approval_decision","schema_version":1,"approval_id":"approval-classification-lower",
            "approver_principal_id":"human-root","target_type":"CLASSIFICATION_LOWER","target_ref":self.claim,
            "decision":"APPROVE","approved_scope":["CLASSIFICATION_LOWER",self.claim],"policy_version":"1","issued_at":now.isoformat(),
        }, "approval-classification-lower-create")
        inspector = InspectService(self.store, self.authority)
        live = inspector.approval(grant_id="inspect-classification-7", task_id="task-7", approval_id="approval-classification-lower")
        self.assertEqual(live["target_type"], "CLASSIFICATION_LOWER")
        with self.assertRaisesRegex(RuntimeDenied, "INSPECT_TASK_SCOPE_MISMATCH"):
            inspector.approval(grant_id="inspect-classification-8", task_id="task-8", approval_id="approval-classification-lower")
        self._classify("class-secret-approval-target", "OBJECT", "secret-approval-target", level="SECRET")
        self.store.put_object(command_id="put-secret-approval-target", object_id="secret-approval-target", payload=b"synthetic secret classification fixture", object_type="artifact", created_by_run="run-7", classification_assertion_ref="class-secret-approval-target")
        self.authority.create_approval({
            "schema_id":"nexus.approval_decision","schema_version":1,"approval_id":"approval-classification-secret",
            "approver_principal_id":"human-root","target_type":"CLASSIFICATION_LOWER","target_ref":"secret-approval-target",
            "decision":"APPROVE","approved_scope":["CLASSIFICATION_LOWER","secret-approval-target"],"policy_version":"1","issued_at":now.isoformat(),
        }, "approval-classification-secret-create")
        with self.assertRaisesRegex(RuntimeDenied, "INSPECT_CLASSIFICATION_OUTSIDE_BOUNDARY"):
            inspector.approval(grant_id="inspect-classification-7", task_id="task-7", approval_id="approval-classification-secret")
        with self.store._connection() as conn:
            conn.execute("UPDATE runs SET status='SUCCEEDED' WHERE run_id='run-7'")
            conn.execute("UPDATE runs SET status='CANCELLED' WHERE run_id='run-pending'")
        plan = self.purge.plan(command_id="classification-purge-plan", plan_id="plan-7", task_id="task-7", target_refs=[self.claim])
        self._approve_purge(plan["plan_hash"])
        self.purge.execute(command_id="classification-purge-execute", record_id="record-7", barrier_id="barrier-7",
            plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7")
        purged = inspector.approval(grant_id="inspect-classification-7", task_id="task-7", approval_id="approval-classification-lower")
        self.assertEqual(purged["target_ref"], "REDACTED_PURGED")
        with self.assertRaisesRegex(RuntimeDenied, "INSPECT_TASK_SCOPE_MISMATCH"):
            inspector.approval(grant_id="inspect-classification-8", task_id="task-8", approval_id="approval-classification-lower")

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

    def test_completed_purge_record_cannot_be_reused_for_another_plan(self):
        plan_a = self.purge.plan(command_id="purge-plan-a", plan_id="plan-a", task_id="task-7", target_refs=[self.claim])
        plan_b = self.purge.plan(command_id="purge-plan-b", plan_id="plan-b", task_id="task-7", target_refs=[self.evidence])
        now = datetime.now(timezone.utc)
        self.authority.create_grant({
            "schema_id":"nexus.delegation_grant","schema_version":1,"grant_id":"grant-plan-b",
            "issued_by":"human-root","granted_to":"agent","task_scope":["task-7"],
            "resource_scope":["plan-a","plan-b","record-x"],"action_scope":["PURGE_EXECUTE"],
            "audience_scope":["nexus-runtime"],"issued_at":now.isoformat(),
            "expires_at":(now+timedelta(days=1)).isoformat(),"status":"ACTIVE","policy_version":"1",
        }, "grant-plan-b")
        for plan, approval_id in ((plan_a, "approval-plan-a"), (plan_b, "approval-plan-b")):
            self.authority.create_approval({
                "schema_id":"nexus.approval_decision","schema_version":1,"approval_id":approval_id,
                "approver_principal_id":"human-root","target_type":"PURGE_EXECUTE","target_ref":plan["plan_id"],
                "effect_id":"record-x","payload_integrity_hash":plan["plan_hash"],"decision":"APPROVE",
                "approved_scope":["PURGE_EXECUTE",plan["plan_id"]],"policy_version":"1","issued_at":now.isoformat(),
            }, approval_id)
        with self.store._connection() as conn:
            conn.execute("UPDATE runs SET status='SUCCEEDED' WHERE run_id='run-7'")
        first = self.purge.execute(command_id="execute-plan-a", record_id="record-x", barrier_id="barrier-a",
            plan=plan_a, grant_id="grant-plan-b", task_id="task-7", approval_id="approval-plan-a")
        self.assertEqual(first["status"], "COMPLETED")
        with self.assertRaisesRegex(RuntimeDenied, "PURGE_EXECUTION_BINDING_MISMATCH"):
            self.purge.execute(command_id="execute-plan-a-wrong-barrier", record_id="record-x", barrier_id="barrier-other",
                plan=plan_a, grant_id="grant-plan-b", task_id="task-7", approval_id="approval-plan-a")
        with self.assertRaisesRegex(RuntimeDenied, "PURGE_EXECUTION_BINDING_MISMATCH"):
            self.purge.execute(command_id="execute-plan-b", record_id="record-x", barrier_id="barrier-b",
                plan=plan_b, grant_id="grant-plan-b", task_id="task-7", approval_id="approval-plan-b")
        self.assertEqual(self.store.get_payload(self.evidence), b"Independent synthetic evidence for governed memory.")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM purge_barriers WHERE barrier_id='barrier-b'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT plan_id FROM purge_execution_records WHERE record_id='record-x'").fetchone()[0], "plan-a")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM purge_execution_records WHERE plan_id='plan-b'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM command_ledger WHERE command_id='execute-plan-b'").fetchone()[0], 0)

    def test_partial_purge_record_cannot_be_reused_for_another_plan(self):
        plan_a = self.purge.plan(command_id="partial-plan-a", plan_id="partial-plan-a", task_id="task-7", target_refs=[self.claim])
        plan_b = self.purge.plan(command_id="partial-plan-b", plan_id="partial-plan-b", task_id="task-7", target_refs=[self.evidence])
        self._seed_unknown_effect()
        now = datetime.now(timezone.utc)
        self.authority.create_grant({
            "schema_id":"nexus.delegation_grant","schema_version":1,"grant_id":"partial-plan-grant",
            "issued_by":"human-root","granted_to":"agent","task_scope":["task-7"],
            "resource_scope":["partial-plan-a","partial-plan-b","partial-record"],"action_scope":["PURGE_EXECUTE"],
            "audience_scope":["nexus-runtime"],"issued_at":now.isoformat(),
            "expires_at":(now+timedelta(days=1)).isoformat(),"status":"ACTIVE","policy_version":"1",
        }, "partial-plan-grant")
        for plan, approval_id in ((plan_a, "partial-approval-a"), (plan_b, "partial-approval-b")):
            self.authority.create_approval({
                "schema_id":"nexus.approval_decision","schema_version":1,"approval_id":approval_id,
                "approver_principal_id":"human-root","target_type":"PURGE_EXECUTE","target_ref":plan["plan_id"],
                "effect_id":"partial-record","payload_integrity_hash":plan["plan_hash"],"decision":"APPROVE",
                "approved_scope":["PURGE_EXECUTE",plan["plan_id"]],"policy_version":"1","issued_at":now.isoformat(),
            }, approval_id)
        partial = self.purge.execute(command_id="partial-execute-a", record_id="partial-record", barrier_id="partial-barrier-a",
            plan=plan_a, grant_id="partial-plan-grant", task_id="task-7", approval_id="partial-approval-a", quiesce_run=lambda _: True)
        self.assertEqual(partial["status"], "PARTIAL")
        with self.assertRaisesRegex(RuntimeDenied, "PURGE_EXECUTION_BINDING_MISMATCH"):
            self.purge.execute(command_id="partial-execute-b", record_id="partial-record", barrier_id="partial-barrier-b",
                plan=plan_b, grant_id="partial-plan-grant", task_id="task-7", approval_id="partial-approval-b")
        self.assertEqual(self.store.get_payload(self.evidence), b"Independent synthetic evidence for governed memory.")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM purge_execution_records WHERE record_id='partial-record'").fetchone()[0], "PARTIAL")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM purge_barriers WHERE barrier_id='partial-barrier-b'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM purge_execution_records WHERE plan_id='partial-plan-b'").fetchone()[0], 0)

    def test_partial_command_is_immutable_and_new_command_resumes_after_active_run_clears(self):
        plan = self.purge.plan(command_id="partial-resume-plan", plan_id="plan-7", task_id="task-7", target_refs=[self.claim])
        self._approve_purge(plan["plan_hash"])
        first = self.purge.execute(command_id="partial-resume-x", record_id="record-7", barrier_id="barrier-7", plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7", quiesce_run=lambda _: False)
        self.assertEqual(first["status"], "PARTIAL")
        exact_replay = self.purge.execute(command_id="partial-resume-x", record_id="record-7", barrier_id="barrier-7", plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7", quiesce_run=lambda _: True)
        self.assertEqual(exact_replay, first)
        with self.store._connection() as conn:
            immutable_x = conn.execute("SELECT request_hash,result_json,result_commitment,result_state FROM command_ledger WHERE command_id='partial-resume-x'").fetchone()
            conn.execute("UPDATE runs SET status='SUCCEEDED' WHERE run_id='run-7'")
        second = self.purge.execute(command_id="partial-resume-y", record_id="record-7", barrier_id="barrier-7", plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7")
        self.assertEqual(second["status"], "COMPLETED")
        historical_x = self.purge.execute(command_id="partial-resume-x", record_id="record-7", barrier_id="barrier-7", plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7")
        self.assertEqual(historical_x, first)
        with self.store._connection() as conn:
            after_x = conn.execute("SELECT request_hash,result_json,result_commitment,result_state FROM command_ledger WHERE command_id='partial-resume-x'").fetchone()
            self.assertEqual(tuple(after_x), tuple(immutable_x))
            self.assertEqual(json.loads(after_x["result_json"])["status"], "PARTIAL")
            self.assertEqual(json.loads(conn.execute("SELECT result_json FROM command_ledger WHERE command_id='partial-resume-y'").fetchone()[0])["status"], "COMPLETED")

    def test_completed_purge_exact_command_replays_after_grant_revocation(self):
        plan = self.purge.plan(command_id="purge-revoke-plan", plan_id="plan-7", task_id="task-7", target_refs=[self.claim])
        self._approve_purge(plan["plan_hash"])
        with self.store._connection() as conn:
            conn.execute("UPDATE runs SET status='SUCCEEDED' WHERE run_id='run-7'")
        request = {"command_id":"purge-revoke-execute", "record_id":"record-7", "barrier_id":"barrier-7", "plan":plan,
                   "grant_id":"grant-7", "task_id":"task-7", "approval_id":"approval-7"}
        original = self.purge.execute(**request)
        self.assertEqual(original["status"], "COMPLETED")
        journal_before = self.purge.journal.read()
        with self.store._connection() as conn:
            purge_state_before = (
                conn.execute("SELECT COUNT(*) FROM purge_ledger").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM purge_execution_records").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM purge_barriers").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM command_ledger WHERE operation='execute_purge'").fetchone()[0],
                [tuple(row) for row in conn.execute("SELECT object_id,payload_state FROM object_states ORDER BY object_id")],
            )
        self.authority.revoke_grant("grant-7", "purge-revoke-root")

        with mock.patch.object(self.purge, "_purge_payloads_and_indexes", side_effect=AssertionError("exact replay must not purge again")):
            self.assertEqual(self.purge.execute(**request), original)
        self.assertEqual(self.purge.journal.read(), journal_before)
        with self.store._connection() as conn:
            self.assertEqual((
                conn.execute("SELECT COUNT(*) FROM purge_ledger").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM purge_execution_records").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM purge_barriers").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM command_ledger WHERE operation='execute_purge'").fetchone()[0],
                [tuple(row) for row in conn.execute("SELECT object_id,payload_state FROM object_states ORDER BY object_id")],
            ), purge_state_before)
        with self.assertRaises(CommandConflict):
            self.purge.execute(**{**request, "record_id":"different-record"})
        with self.assertRaises(CommandConflict):
            self.purge.execute(**{**request, "barrier_id":"different-barrier"})
        changed_plan = {**plan, "plan_id":"different-plan"}
        with self.assertRaisesRegex(RuntimeDenied, "PURGE_PLAN_HASH_MISMATCH"):
            self.purge.execute(**{**request, "plan":changed_plan})
        with self.assertRaises(AuthorizationDenied):
            self.purge.execute(**{**request, "command_id":"purge-revoke-new-command", "record_id":"new-record", "barrier_id":"new-barrier"})
        self.assertEqual(self.store.get_object_metadata(self.claim), {"payload_state": "PURGED"})
        with self.store._connection() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM purge_barriers WHERE barrier_id='new-barrier'").fetchone())
            self.assertIsNone(conn.execute("SELECT 1 FROM command_ledger WHERE command_id='purge-revoke-new-command'").fetchone())

    def test_completed_purge_exact_command_replays_after_approval_expiry(self):
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=2)
        plan = self.purge.plan(command_id="purge-expiry-plan", plan_id="plan-7", task_id="task-7", target_refs=[self.claim])
        self._approve_purge(plan["plan_hash"], expires_at=expires_at.isoformat())
        with self.store._connection() as conn:
            conn.execute("UPDATE runs SET status='SUCCEEDED' WHERE run_id='run-7'")
        request = {"command_id":"purge-expiry-execute", "record_id":"record-7", "barrier_id":"barrier-7", "plan":plan,
                   "grant_id":"grant-7", "task_id":"task-7", "approval_id":"approval-7"}
        original = self.purge.execute(**request)
        later = expires_at + timedelta(seconds=5)
        with mock.patch("kernel.authority.service._now", return_value=later):
            self.assertEqual(self.purge.execute(**request), original)

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
        self.assertEqual(self.store.get_object_metadata("manifest-7"), {"payload_state": "PURGED"})
        tombstone = self.store.get_object_metadata(self.claim)
        self.assertEqual(tombstone, {"payload_state": "PURGED"})
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
            trace_fact = conn.execute("SELECT event_json FROM trace_events WHERE event_id='evt-purge-ref-event'").fetchone()[0]
            self.assertNotIn("https://private.example/recipient/42", trace_fact)
            self.assertNotIn(purged_payload_hash, trace_fact)
            self.assertEqual(json.loads(trace_fact)["typed_metadata"], {
                "execution_state": "CANCELLED",
                "effect_outcome": "NOT_COMMITTED",
                "reconciliation_status": "NOT_REQUIRED",
            })
            self.assertNotIn(effect_id, trace_fact)
            self.assertEqual((approval_fact["decision"], approval_fact["approver_principal_id"], approval_fact["target_ref"], approval_fact["payload_integrity_hash"], approval_fact["approved_scope_json"]), ("APPROVE", "human-root", "REDACTED_PURGED", None, '[]'))
            self.assertTrue(conn.execute("SELECT 1 FROM json_each(?, '$.object_refs') WHERE value='REDACTED_PURGED'", (trace_fact,)).fetchone())
            purge_ref = conn.execute("SELECT payload_uri,integrity_hash FROM purge_execution_refs WHERE record_id='record-7' AND object_id=?", (self.claim,)).fetchone()
            self.assertEqual((purge_ref["payload_uri"], purge_ref["integrity_hash"]), (None, None))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM run_manifest_inputs WHERE input_object_id IN (?,?) OR manifest_object_id IN (?,?)", (self.claim,"manifest-7",self.claim,"manifest-7")).fetchone()[0], 0)
            self.assertNotIn("https://private.example/recipient/42", json.dumps([tuple(row) for row in conn.execute("SELECT target_ref,payload_integrity_hash FROM effects UNION ALL SELECT target_ref,payload_integrity_hash FROM approval_decisions")]))
            self.assertNotIn(purged_payload_hash, json.dumps([tuple(row) for row in conn.execute("SELECT target_ref,payload_integrity_hash FROM effects UNION ALL SELECT target_ref,payload_integrity_hash FROM approval_decisions")]))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE runs SET status='READY' WHERE run_id='run-pending'")
        restored = ObjectStore(backup_root, independent_purge_journal_path=self.journal_path)
        try:
            restored_authority = AuthorityService(restored, self.authority.policy)
            restored_memory = MemoryService(restored, restored_authority, VerificationService(restored, restored_authority))
            restored_purge = PurgeService(restored, restored_authority, restored_memory, independent_journal_path=self.journal_path)
            RuntimeModeService(restored, restored_authority).complete_validated_recovery(command_id="finish-backup-purge-recovery", purge_service=restored_purge)
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

    def test_proof_purge_redacts_governed_derivatives_and_replay_results(self):
        self.memory.retain_raw(command_id="retain-proof-claim", object_id=self.claim, run_id="run-7")
        integrity = self.verifier.verify_object_integrity(verification_id="verify-proof-integrity", target_ref=self.claim, evidence_refs=[self.evidence], run_id="run-7")
        self.assertEqual(integrity["verdict"], "PASS")
        human = self._human_verification("verify-proof-human")
        candidate = self.memory.create_candidate(command_id="candidate-proof", candidate_id="candidate-7", claim_ref=self.claim, evidence_refs=[self.evidence], owner="agent", classification_assertion_ref="class-claim-7", verification_ref=human["verification_id"], review_trigger="purge proof")
        self.assertEqual(candidate["status"], "ADMITTED")
        # Add representative durable references outside the memory tables.  The
        # derived route object enters the real purge closure; the "supports"
        # relation is deliberately a non-closure reference to the tombstone.
        self.store.create_logical_ref(command_id="proof-logical-ref", ref_id="proof:claim", ref_type="artifact", object_id=self.claim, updated_by_run="run-7")
        related_id = self._put("related-object-7", b"A synthetic object with a non-lineage reference.")
        self.store.add_relation(command_id="proof-supports-claim", from_id=related_id, relation_type="supports", to_id=self.claim)
        with self.store._connection() as conn:
            conn.execute("INSERT INTO classification_assertions(assertion_id,subject_type,subject_ref,sensitivity_level,handling_tags_json,policy_version,reason,actor_id,supersedes) VALUES('class-claim-reason','OBJECT',?,'PUBLIC','[]','1',?,'agent','class-claim-7')", (self.claim, f"Synthetic reason names {self.claim} and {self.evidence}."))
        self._classify("class-route-proof", "OBJECT", "route-object-proof")
        route_payload = json.dumps({"schema_id":"nexus.route_decision","schema_version":1,"route_decision_id":"route-object-proof","run_or_subtask_id":"subtask-pending","route_policy_version":"1","task_class":"test","risk_class":"LOW","quality_requirement":"ROUTINE","user_policy_ref":"1","eligible_models":[],"excluded_models":[],"exclusion_reasons":[],"selected_model_class":"E0","selected_model_id":"synthetic-model","selected_model_profile_version":"1","reason_codes":["SYNTHETIC_PROOF"],"budget_snapshot":{"account_id":"proof-account","unit":"test","limit":0,"reserved":0,"consumed":0,"remaining":0,"model_calls_remaining":0,"tool_calls_remaining":0,"child_runs_remaining":0},"escalation_allowed":False,"created_at":datetime.now(timezone.utc).isoformat()}).encode()
        self.store.put_object(command_id="put-route-object-proof", object_id="route-object-proof", payload=route_payload, object_type="artifact", created_by_run="run-pending", classification_assertion_ref="class-route-proof", derived_from=[self.claim])
        with self.store._connection() as conn:
            conn.execute("INSERT INTO subtasks(subtask_id,task_id,node_index,node_json,status,scheduled_run_id,command_id,created_at) VALUES('subtask-pending','task-7',0,'{}','READY','run-pending','proof-subtask','2026-01-01T00:00:00Z')")
            self.store._record_command(conn, "proof-dag", "create_task_dag", "b" * 64, {"task_id":"task-7"})
            conn.execute("INSERT INTO task_dags(task_id,root_run_id,dag_version,graph_hash,node_count,command_id,created_at) VALUES('task-7','run-7','proof',?,1,'proof-dag','2026-01-01T00:00:00Z')", ("c" * 64,))
            self.store._record_command(conn, "proof-route-row", "schedule_route_decision", "a" * 64, {"decision_object_id":"route-object-proof"})
            conn.execute("INSERT INTO route_decisions(route_decision_id,subtask_id,decision_object_id,decision_json,command_id,created_at) VALUES('route-object-proof','subtask-pending','route-object-proof',?,'proof-route-row','2026-01-01T00:00:00Z')", (route_payload.decode(),))
            conn.execute("INSERT INTO subtask_attempts(attempt_id,task_id,subtask_id,attempt_no,run_id,route_decision_ref,requested_capability,attempt_reason,predecessor_attempt_id,outcome,command_id,created_at) VALUES('proof-attempt','task-7','subtask-pending',1,'run-pending','route-object-proof','TOOL','INITIAL',NULL,'READY','proof-attempt-command','2026-01-01T00:00:00Z')")
        approval_id, effect_id, effect_inspector = self._seed_purge_identifier_audit()
        inspect_grant_id = "inspect-proof-task"
        now = datetime.now(timezone.utc)
        self.authority.create_grant({"schema_id":"nexus.delegation_grant", "schema_version":1, "grant_id":inspect_grant_id, "issued_by":"human-root", "granted_to":"agent", "task_scope":["task-7"], "resource_scope":["task:task-7","route:route-object-proof","object:claim-7","object:manifest-7","approval:approval-verify-proof-human"], "action_scope":["INSPECT"], "audience_scope":["nexus-inspect"], "issued_at":now.isoformat(), "expires_at":(now+timedelta(days=1)).isoformat(), "status":"ACTIVE", "policy_version":"1"}, "create-inspect-proof-grant")

        with self.store._connection() as conn:
            conn.execute("UPDATE runs SET status='SUCCEEDED' WHERE run_id='run-7'")

        # Preserve a real pre-purge database/object snapshot; the independent
        # journal remains external and will later be replayed onto this copy.
        backup_root = self.root / "proof-old-backup"
        (backup_root / "objects").mkdir(parents=True)
        destination = sqlite3.connect(backup_root / "nexus.sqlite")
        try:
            with self.store._connection() as source:
                source.backup(destination)
        finally:
            destination.close()
        shutil.copytree(self.data_root / "objects", backup_root / "objects", dirs_exist_ok=True)

        plan = self.purge.plan(command_id="proof-purge-plan", plan_id="plan-7", task_id="task-7", target_refs=[self.claim, self.evidence])
        self._approve_purge(plan["plan_hash"])
        outcome = self.purge.execute(command_id="proof-purge-execute", record_id="record-7", barrier_id="barrier-7", plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7")
        self.assertEqual(outcome["status"], "COMPLETED")

        def residue(store):
            with store._connection() as conn:
                verification_rows = [dict(row) for row in conn.execute("SELECT verification_id,target_ref,evidence_used_json,result_json FROM verification_results WHERE verification_id IN ('verify-proof-integrity','verify-proof-human') ORDER BY verification_id")]
                for row in verification_rows:
                    result_doc = json.loads(row["result_json"])
                    row["missing_evidence"] = result_doc.get("missing_evidence")
                    row["conflicts"] = result_doc.get("conflicts")
                candidate_row = dict(conn.execute("SELECT candidate_id,claim_ref,metadata_json,classification_assertion_ref,verification_ref,status FROM memory_candidates WHERE candidate_id='candidate-7'").fetchone())
                evidence_rows = [dict(row) for row in conn.execute("SELECT candidate_id,evidence_object_id FROM memory_candidate_evidence WHERE candidate_id='candidate-7'")]
                effect_row = dict(conn.execute("SELECT payload_object_ref,target_ref,payload_integrity_hash,idempotency_key,effect_json,external_receipt_ref FROM effects WHERE effect_id='effect-purge-sensitive'").fetchone())
                approval_rows = [dict(row) for row in conn.execute("SELECT approval_id,target_ref,effect_id,payload_integrity_hash,approved_scope_json,reason,request_ref FROM approval_decisions WHERE approval_id IN ('approval-verify-proof-human','approval-purge-sensitive','approval-7') ORDER BY approval_id")]
                classification_rows = [dict(row) for row in conn.execute("SELECT assertion_id,subject_type,subject_ref,reason,supersedes FROM classification_assertions WHERE subject_ref IN (?,?) OR assertion_id='class-claim-reason' ORDER BY assertion_id", (self.claim,self.evidence))]
                logical_rows = [dict(row) for row in conn.execute("SELECT ref_id,current_object_id,revision FROM logical_refs WHERE ref_id='proof:claim'")]
                relation_rows = [dict(row) for row in conn.execute("SELECT from_id,relation_type,to_id FROM object_relations WHERE from_id IN (?,?) OR to_id IN (?,?) ORDER BY from_id,relation_type,to_id", (self.claim,self.evidence,self.claim,self.evidence))]
                runtime_rows = {
                    "runs": [dict(row) for row in conn.execute("SELECT run_id,manifest_ref FROM runs WHERE run_id IN ('run-7','run-pending') ORDER BY run_id")],
                    "run_manifest_inputs": [dict(row) for row in conn.execute("SELECT run_id,manifest_object_id,input_object_id FROM run_manifest_inputs WHERE input_object_id IN (?,?) OR manifest_object_id IN (?,?,?)", (self.claim,self.evidence,"manifest-7","manifest-pending","route-object-proof"))],
                    "route_decisions": [dict(row) for row in conn.execute("SELECT decision_object_id,decision_json FROM route_decisions WHERE decision_object_id='route-object-proof'")],
                    "subtask_attempts": [dict(row) for row in conn.execute("SELECT route_decision_ref FROM subtask_attempts WHERE attempt_id='proof-attempt'")],
                }
                command_rows = [dict(row) for row in conn.execute("SELECT command_id,operation,request_hash,result_json FROM command_ledger ORDER BY command_id")]
                states = {row["object_id"]:row["payload_state"] for row in conn.execute("SELECT object_id,payload_state FROM object_states WHERE object_id IN (?,?,?,?,?)", (self.claim,self.evidence,"manifest-7","manifest-pending","route-object-proof"))}
                effect_refs = [dict(row) for row in conn.execute("SELECT record_id,object_id,payload_uri,integrity_hash FROM purge_execution_refs WHERE object_id IN (?,?)", (self.claim,self.evidence))]
                barrier_refs = [dict(row) for row in conn.execute("SELECT barrier_id,object_id FROM purge_barrier_refs WHERE object_id IN (?,?)", (self.claim,self.evidence))]
                purge_control = {
                    "plans": [dict(row) for row in conn.execute("SELECT plan_id,task_id,plan_hash,plan_json FROM purge_plan_records WHERE plan_id='plan-7'")],
                    "executions": [dict(row) for row in conn.execute("SELECT record_id,plan_id,barrier_id,status,record_json FROM purge_execution_records WHERE record_id='record-7'")],
                    "execution_refs": effect_refs,
                    "barrier_refs": barrier_refs,
                    "barrier": [dict(row) for row in conn.execute("SELECT barrier_id,plan_id,status FROM purge_barriers WHERE barrier_id='barrier-7'")],
                }
                # Scan every persisted user-table text value for these governed
                # IDs, including JSON/nested JSON fields not anticipated above.
                occurrences = {}
                object_ids = (self.claim,self.evidence,"manifest-7","manifest-pending","route-object-proof")
                for table_row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%_fts_%'"):
                    table = table_row[0]
                    escaped_table = table.replace('"','""')
                    for col_row in conn.execute(f'PRAGMA table_info("{escaped_table}")'):
                        column = col_row[1]
                        escaped_column = column.replace('"','""')
                        for object_id in object_ids:
                            try:
                                if column.endswith("_json"):
                                    needle = json.dumps(object_id)
                                    count = conn.execute(f'SELECT COUNT(*) FROM "{escaped_table}" WHERE instr(CAST("{escaped_column}" AS TEXT),?)>0', (needle,)).fetchone()[0]
                                elif column in {"reason", "request_ref"}:
                                    count = conn.execute(f'SELECT COUNT(*) FROM "{escaped_table}" WHERE instr(CAST("{escaped_column}" AS TEXT),?)>0', (object_id,)).fetchone()[0]
                                else:
                                    count = conn.execute(f'SELECT COUNT(*) FROM "{escaped_table}" WHERE CAST("{escaped_column}" AS TEXT)=?', (object_id,)).fetchone()[0]
                            except sqlite3.DatabaseError:
                                continue
                            if count:
                                occurrences[f"{table}.{column}:{object_id}"] = count
            return verification_rows,candidate_row,evidence_rows,command_rows,states,effect_row,approval_rows,classification_rows,logical_rows,relation_rows,runtime_rows,purge_control,occurrences

        verified, memory_row, candidate_evidence, commands, object_states, effect_row, approval_rows, classification_rows, logical_rows, relation_rows, runtime_rows, purge_control, occurrences = residue(self.store)
        self.assertEqual(object_states, {self.claim:"PURGED", self.evidence:"PURGED", "manifest-7":"PURGED", "manifest-pending":"PURGED", "route-object-proof":"PURGED"})
        self.assertEqual(len(verified), 2)
        for row in verified:
            self.assertEqual(row["target_ref"], "REDACTED_PURGED")
            self.assertNotIn(self.evidence, row["evidence_used_json"])
            self.assertNotIn(self.claim, row["result_json"])
            self.assertNotIn(self.evidence, row["result_json"])
            self.assertNotIn(self.claim, json.dumps(row["missing_evidence"]))
            self.assertNotIn(self.evidence, json.dumps(row["missing_evidence"]))
            self.assertNotIn(self.claim, json.dumps(row["conflicts"]))
            self.assertNotIn(self.evidence, json.dumps(row["conflicts"]))
        self.assertIsNone(memory_row["claim_ref"])
        self.assertEqual(memory_row["status"], "PURGED")
        metadata_projection = json.dumps(json.loads(memory_row["metadata_json"]), separators=(",", ":"))
        self.assertNotIn('"' + self.claim + '"', metadata_projection)
        self.assertNotIn('"' + self.evidence + '"', metadata_projection)
        self.assertEqual(candidate_evidence, [])
        self.assertEqual(memory_row["classification_assertion_ref"], "class-claim-7")
        self.assertEqual(memory_row["verification_ref"], "verify-proof-human")
        self.assertNotIn(self.claim, {row["subject_ref"] for row in classification_rows})
        self.assertFalse(any(self.claim in row["reason"] or self.evidence in row["reason"] for row in classification_rows))
        self.assertTrue(any(row["supersedes"] == "class-claim-7" for row in classification_rows))
        self.assertEqual(logical_rows, [])
        self.assertEqual(relation_rows, [])
        self.assertNotIn("manifest-7", {row["manifest_ref"] for row in runtime_rows["runs"]})
        self.assertEqual({row["manifest_ref"] for row in runtime_rows["runs"]}, {"REDACTED_PURGED"})
        self.assertEqual(runtime_rows["run_manifest_inputs"], [])
        self.assertEqual(runtime_rows["route_decisions"], [])
        self.assertEqual(runtime_rows["subtask_attempts"], [{"route_decision_ref":None}])
        self.assertIsNone(effect_row["payload_object_ref"])
        self.assertEqual(effect_row["target_ref"], "REDACTED_PURGED")
        self.assertEqual(effect_row["payload_integrity_hash"], "0" * 64)
        self.assertTrue(effect_row["idempotency_key"].startswith("REDACTED_PURGED:"))
        self.assertIn("REDACTED_PURGED", effect_row["effect_json"])
        self.assertIsNone(effect_row["external_receipt_ref"])
        self.assertNotIn(self.claim, effect_row["effect_json"])
        self.assertNotIn(self.evidence, effect_row["effect_json"])
        approval_by_id = {row["approval_id"]:row for row in approval_rows}
        self.assertEqual(approval_by_id["approval-verify-proof-human"]["target_ref"], "REDACTED_PURGED")
        self.assertIsNone(approval_by_id["approval-verify-proof-human"]["effect_id"])
        self.assertEqual(approval_by_id["approval-purge-sensitive"]["target_ref"], "REDACTED_PURGED")
        self.assertEqual(approval_by_id["approval-purge-sensitive"]["payload_integrity_hash"], None)
        self.assertEqual(approval_by_id["approval-purge-sensitive"]["approved_scope_json"], "[]")
        self.assertIsNone(approval_by_id["approval-purge-sensitive"]["reason"])
        self.assertIsNone(approval_by_id["approval-purge-sensitive"]["request_ref"])
        self.assertNotIn("verification_results.target_ref:claim-7", occurrences)
        self.assertNotIn("memory_candidates.metadata_json:claim-7", occurrences)

        # Normal read APIs/inspect and the paused verifier replay candidate are
        # measured without changing those APIs in this proof-only task.
        self.assertEqual(self.verifier.get("verify-proof-integrity")["target_ref"], "REDACTED_PURGED")
        self.assertNotIn(self.claim, json.dumps(self.verifier.get("verify-proof-integrity")))
        verification_replay = self.verifier.verify_object_integrity(verification_id="verify-proof-integrity", target_ref=self.claim, evidence_refs=[self.evidence], run_id="run-7")
        self.assertNotIn(self.claim, json.dumps(verification_replay))
        self.assertNotIn(self.evidence, json.dumps(verification_replay))
        with self.assertRaises(CommandConflict):
            self.verifier.verify_object_integrity(verification_id="verify-proof-integrity", target_ref="different-purged-target", evidence_refs=[self.evidence], run_id="run-7")
        with self.store._connection() as conn:
            before_replay_counts = {
                "commands": conn.execute("SELECT COUNT(*) FROM command_ledger").fetchone()[0],
                "purge_ledger": conn.execute("SELECT COUNT(*) FROM purge_ledger").fetchone()[0],
                "executions": conn.execute("SELECT COUNT(*) FROM purge_execution_records").fetchone()[0],
            }
        journal_count = len(self.purge.journal.read())
        # Object creation's committed ledger replay is reachable after purge
        # and returns its original object identity. Logical-ref replay returns
        # only its revision; there is no public logical-ref getter.
        self.assertEqual(self.store.put_object(command_id="put-claim-7", object_id=self.claim, payload=b"The synthetic Nexus fact is governed memory.", object_type="artifact", created_by_run="run-7", classification_assertion_ref="class-claim-7"), "REDACTED_PURGED")
        relation_replay = self.store.add_relation(command_id="proof-supports-claim", from_id=related_id, relation_type="supports", to_id=self.claim)
        self.assertEqual(relation_replay["to_id"], "REDACTED_PURGED")
        self.assertEqual(self.store.create_logical_ref(command_id="proof-logical-ref", ref_id="proof:claim", ref_type="artifact", object_id=self.claim, updated_by_run="run-7"), 1)
        self.assertIsNone(self.memory.retain_raw(command_id="retain-proof-claim", object_id=self.claim, run_id="run-7"))
        self.assertEqual(self.purge.execute(command_id="proof-purge-execute", record_id="record-7", barrier_id="barrier-7", plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7"), outcome)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM command_ledger").fetchone()[0], before_replay_counts["commands"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM purge_ledger").fetchone()[0], before_replay_counts["purge_ledger"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM purge_execution_records").fetchone()[0], before_replay_counts["executions"])
        self.assertEqual(len(self.purge.journal.read()), journal_count)
        with self.assertRaises(PurgedObject):
            self.store.get_payload(self.claim)
        proof_inspector = InspectService(self.store, self.authority)
        # Purged route references are stable redacted projections and no
        # longer require deleted envelope/classification rows.
        task_view = proof_inspector.task(grant_id=inspect_grant_id, task_id="task-7")
        self.assertTrue(any(attempt["route_decision_ref"] == "REDACTED_PURGED" for attempt in task_view["attempts"]))
        object_view = proof_inspector.object_metadata(grant_id=inspect_grant_id, task_id="task-7", object_id=self.claim)
        self.assertEqual((object_view["object_id"], object_view["payload_state"]), (None, "PURGED"))
        manifest_view = proof_inspector.object_metadata(grant_id=inspect_grant_id, task_id="task-7", object_id="manifest-7")
        self.assertEqual((manifest_view["object_id"], manifest_view["payload_state"]), (None, "PURGED"))
        human_approval_view = proof_inspector.approval(grant_id=inspect_grant_id, task_id="task-7", approval_id="approval-verify-proof-human")
        self.assertEqual(human_approval_view["target_ref"], "REDACTED_PURGED")
        self.assertEqual(human_approval_view["effect_id"], "REDACTED_PURGED")
        self.assertEqual(human_approval_view["approved_scope"], ["VERIFY"])
        effect_view = effect_inspector.effect(grant_id="purge-audit-inspect", task_id="task-7", effect_id="effect-purge-sensitive")
        self.assertEqual(effect_view["target_ref"], "REDACTED_PURGED")
        approval_view = effect_inspector.approval(grant_id="purge-audit-inspect", task_id="task-7", approval_id=approval_id, include_payload_hash=True)
        self.assertEqual(approval_view["target_ref"], "REDACTED_PURGED")
        trace_view = effect_inspector.trace_events(grant_id="purge-audit-inspect", task_id="task-7", run_id="run-7")
        self.assertTrue(any("REDACTED_PURGED" in event["object_refs"] for event in trace_view))
        self.assertFalse(any(self.claim in json.dumps(event) or self.evidence in json.dumps(event) for event in trace_view))
        route_view = effect_inspector.route(grant_id=inspect_grant_id, task_id="task-7", route_id="route-object-proof")
        self.assertEqual(route_view, {"route_decision_id": "REDACTED_PURGED", "status": "REDACTED_PURGED"})
        self.assertEqual(self.memory.search_raw(query="synthetic Nexus fact", run_id="run-7"), [])
        self.assertEqual(self.memory.search_admitted(query="synthetic Nexus fact", run_id="run-7"), [])
        candidate_replay = self.memory.create_candidate(command_id="candidate-proof", candidate_id="candidate-7", claim_ref=self.claim, evidence_refs=[self.evidence], owner="agent", classification_assertion_ref="class-claim-7", verification_ref="verify-proof-human", review_trigger="purge proof")
        self.assertNotIn('"' + self.claim + '"', json.dumps(candidate_replay))
        self.assertNotIn('"' + self.evidence + '"', json.dumps(candidate_replay))

        command_ids = {row["command_id"] for row in commands}
        self.assertIn("candidate-proof", command_ids)
        self.assertIn("verify-verify-proof-integrity", command_ids)
        command_by_id = {row["command_id"]:row for row in commands}
        self.assertNotIn(self.claim, command_by_id["put-claim-7"]["result_json"])
        self.assertNotIn(self.evidence, command_by_id["put-evidence-7"]["result_json"])
        self.assertNotIn(self.claim, command_by_id["retain-proof-claim"]["result_json"])
        self.assertIn("verify-proof-integrity", command_by_id["verify-verify-proof-integrity"]["result_json"])
        self.assertNotIn(self.claim, command_by_id["verify-verify-proof-integrity"]["result_json"])
        self.assertIn("candidate-7", command_by_id["candidate-proof"]["result_json"])
        purge_result = json.loads(command_by_id["proof-purge-execute"]["result_json"])
        self.assertTrue(purge_result["purged_refs"])
        self.assertTrue(all(ref == "REDACTED_PURGED" for ref in purge_result["purged_refs"]))
        self.assertTrue(all(row["request_hash"] for row in commands))
        # request_hash is an opaque commitment; no attempt is made to recover
        # its input from the digest.
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(set(self.purge.journal.read()[-1]["protected_refs"]), set(object_states))
        self.assertEqual(self.store.get_object_metadata(self.claim)["payload_state"], "PURGED")
        self.assertFalse(hasattr(self.store, "get_logical_ref"))

        restored = ObjectStore(backup_root, independent_purge_journal_path=self.journal_path)
        try:
            restored_authority = AuthorityService(restored, self.authority.policy)
            restored_memory = MemoryService(restored, restored_authority, VerificationService(restored, restored_authority))
            restored_purge = PurgeService(restored, restored_authority, restored_memory, independent_journal_path=self.journal_path)
            RuntimeModeService(restored, restored_authority).complete_validated_recovery(command_id="finish-proof-purge-recovery", purge_service=restored_purge)
            replay = restored_purge.replay_independent_journal()
            self.assertTrue(replay["normal_allowed"])
            restored_residue = residue(restored)
            r_verified, r_candidate, r_evidence, r_commands, r_states, r_effect, r_approvals, r_classifications, r_logicals, r_relations, r_runtime, r_purge, r_occurrences = restored_residue
            self.assertEqual(r_states, {self.claim:"PURGED", self.evidence:"PURGED", "manifest-7":"PURGED", "manifest-pending":"PURGED", "route-object-proof":"PURGED"})
            self.assertEqual([(row["verification_id"],row["target_ref"],row["evidence_used_json"],row["result_json"]) for row in r_verified], [(row["verification_id"],row["target_ref"],row["evidence_used_json"],row["result_json"]) for row in verified])
            self.assertEqual((r_candidate["claim_ref"],r_candidate["metadata_json"],r_candidate["status"]), (memory_row["claim_ref"],memory_row["metadata_json"],"PURGED"))
            self.assertEqual(r_evidence, candidate_evidence)
            self.assertTrue(all(row["request_hash"] for row in r_commands))
            restored_command_ids = {row["command_id"] for row in r_commands}
            self.assertNotIn("proof-purge-plan", restored_command_ids)
            self.assertNotIn("proof-purge-execute", restored_command_ids)
            self.assertEqual(r_effect, effect_row)
            pre_snapshot_approvals = [row for row in approval_rows if row["approval_id"] != "approval-7"]
            self.assertEqual(r_approvals, pre_snapshot_approvals)
            self.assertEqual(r_classifications, classification_rows)
            self.assertEqual(r_logicals, logical_rows)
            self.assertEqual(r_relations, relation_rows)
            self.assertEqual(r_runtime, runtime_rows)
            # The external journal replay reconstructs the barrier projection,
            # not the post-snapshot plan/execution CommandLedger documents.
            self.assertEqual(r_purge["plans"], [])
            self.assertEqual(r_purge["executions"], [])
            self.assertEqual(r_purge["execution_refs"], [])
            self.assertEqual(r_purge["barrier_refs"], purge_control["barrier_refs"])
            self.assertEqual(r_purge["barrier"], purge_control["barrier"])
            self.assertEqual(set(restored_purge.journal.read()[-1]["protected_refs"]), set(r_states))
            with restored._connection() as conn:
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            post_snapshot_tables = {"command_ledger", "purge_plan_records", "purge_execution_records", "purge_execution_refs"}
            stable_live = {key:value for key,value in occurrences.items() if key.split(".",1)[0] not in post_snapshot_tables}
            stable_restored = {key:value for key,value in r_occurrences.items() if key.split(".",1)[0] not in post_snapshot_tables}
            self.assertEqual(stable_restored, stable_live)
            self.assertFalse(any(key.startswith(("purge_plan_records.","purge_execution_records.","purge_execution_refs.")) for key in r_occurrences))
            with self.assertRaises(PurgedObject):
                restored.get_payload(self.claim)
            restored_verifier = VerificationService(restored, self.authority)
            self.assertEqual(restored_verifier.get("verify-proof-integrity")["evidence_used"], ["REDACTED_PURGED"])
            restored_command_count = len(r_commands)
            replayed_integrity = restored_verifier.verify_object_integrity(verification_id="verify-proof-integrity", target_ref=self.claim, evidence_refs=[self.evidence], run_id="run-7")
            self.assertNotIn('"' + self.claim + '"', json.dumps(replayed_integrity))
            self.assertNotIn('"' + self.evidence + '"', json.dumps(replayed_integrity))
            self.assertEqual(restored.put_object(command_id="put-claim-7", object_id=self.claim, payload=b"The synthetic Nexus fact is governed memory.", object_type="artifact", created_by_run="run-7", classification_assertion_ref="class-claim-7"), "REDACTED_PURGED")
            self.assertEqual(restored.add_relation(command_id="proof-supports-claim", from_id=related_id, relation_type="supports", to_id=self.claim)["to_id"], "REDACTED_PURGED")
            self.assertEqual(restored.create_logical_ref(command_id="proof-logical-ref", ref_id="proof:claim", ref_type="artifact", object_id=self.claim, updated_by_run="run-7"), 1)
            restored_candidate = restored_memory.create_candidate(command_id="candidate-proof", candidate_id="candidate-7", claim_ref=self.claim, evidence_refs=[self.evidence], owner="agent", classification_assertion_ref="class-claim-7", verification_ref="verify-proof-human", review_trigger="purge proof")
            self.assertNotIn('"' + self.claim + '"', json.dumps(restored_candidate))
            self.assertNotIn('"' + self.evidence + '"', json.dumps(restored_candidate))
            with restored._connection() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM command_ledger").fetchone()[0], restored_command_count)
            restored_inspector = InspectService(restored, restored_authority)
            restored_task_view = restored_inspector.task(grant_id=inspect_grant_id, task_id="task-7")
            self.assertTrue(any(item["route_decision_ref"] == "REDACTED_PURGED" for item in restored_task_view["attempts"]))
            restored_object_view = restored_inspector.object_metadata(grant_id=inspect_grant_id, task_id="task-7", object_id="manifest-7")
            self.assertEqual((restored_object_view["object_id"], restored_object_view["payload_state"]), (None, "PURGED"))
            self.assertEqual(restored_inspector.effect(grant_id="purge-audit-inspect", task_id="task-7", effect_id="effect-purge-sensitive")["target_ref"], "REDACTED_PURGED")
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
        with self.store._connection() as conn:
            conn.execute("UPDATE runs SET status='SUCCEEDED' WHERE run_id='run-7'")

        snapshot_root = self.root / "recovery-snapshot"
        (snapshot_root / "objects").mkdir(parents=True)
        destination = sqlite3.connect(snapshot_root / "nexus.sqlite")
        try:
            with self.store._connection() as source:
                source.backup(destination)
        finally:
            destination.close()
        snapshot_conn = sqlite3.connect(snapshot_root / "nexus.sqlite")
        try:
            self.assertEqual(snapshot_conn.execute("SELECT mode FROM runtime_mode_state").fetchone()[0], "NORMAL")
        finally:
            snapshot_conn.close()
        shutil.copytree(self.data_root / "objects", snapshot_root / "objects", dirs_exist_ok=True)

        outcome = self.purge.execute(command_id="recovery-purge-execute", record_id="record-7", barrier_id="barrier-7", plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7")
        self.assertEqual(outcome["status"], "COMPLETED")

        restored_root = self.root / "restored-old-snapshot"
        shutil.copytree(snapshot_root, restored_root)
        # The snapshot deliberately remains NORMAL and is opened through the ordinary
        # production path; stale external journal freshness must force Recovery.
        restored = ObjectStore(restored_root, policy=self.authority.policy, independent_purge_journal_path=self.journal_path)
        try:
            restored_authority = AuthorityService(restored, self.authority.policy)
            self.assertEqual(RuntimeModeService(restored, restored_authority).current()["mode"], "RECOVERY")
            restored_memory = MemoryService(restored, restored_authority, VerificationService(restored, restored_authority))
            restored_purge = PurgeService(restored, restored_authority, restored_memory, independent_journal_path=self.journal_path)
            restored_modes = RuntimeModeService(restored, restored_authority)
            with self.assertRaisesRegex(RuntimeDenied, "RUNTIME_RECOVERY_CORE_BYPASS"):
                restored.get_object_metadata(self.claim)
            with self.assertRaisesRegex(RuntimeDenied, "RUNTIME_RECOVERY_CORE_BYPASS"):
                restored.get_payload(self.claim)
            with self.assertRaisesRegex(RuntimeDenied, "RUNTIME_RECOVERY_CORE_BYPASS"):
                restored_memory.search_raw(query="governed memory", run_id="run-7")
            with self.assertRaisesRegex(RuntimeDenied, "RUNTIME_RECOVERY_CORE_BYPASS"):
                InspectService(restored, restored_authority).task(grant_id="grant-7", task_id="task-7")
            with self.assertRaisesRegex(RuntimeDenied, "RUNTIME_RECOVERY_CORE_BYPASS"):
                TraceRuntime(restored, restored_authority).create_task(
                    {"schema_id":"nexus.task","schema_version":1,"task_id":"recovery-blocked-task",
                     "requester_id":"human-root","status":"CREATED","created_at":datetime.now(timezone.utc).isoformat(),
                     "command_id":"recovery-blocked-task-command"}
                )
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
                watermark = conn.execute("SELECT sequence,record_hash FROM independent_purge_journal_watermark WHERE singleton=1").fetchone()
                journal_head = self.purge.journal.verified_head()
                self.assertEqual(tuple(watermark), journal_head)
            self.assertEqual(restored_modes.current()["mode"], "NORMAL")
            restored.close()
            reopened = ObjectStore(restored_root, policy=self.authority.policy, independent_purge_journal_path=self.journal_path)
            try:
                self.assertEqual(RuntimeModeService(reopened, restored_authority).current()["mode"], "NORMAL")
            finally:
                reopened.close()
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
