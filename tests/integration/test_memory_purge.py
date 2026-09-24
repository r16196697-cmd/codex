import json
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from adapters.storage import ObjectStore
from adapters.client.operator import OperatorClient
from kernel.authority.errors import AuthorizationDenied
from kernel.object.errors import PurgedObject, PurgeBarrierActive
from kernel.authority import AuthorityService
from kernel.memory import MemoryService
from kernel.purge import PurgeService
from kernel.runtime.errors import RuntimeDenied
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
        self.actions = ["RUN_CREATE", "VERIFY", "MEMORY_RETAIN", "MEMORY_ADMIT", "MEMORY_SEARCH", "PURGE_EXECUTE"]
        self.resources = ["run-7", "claim-7", "evidence-7", "manifest-7", "candidate-7", "candidate-quarantine", "candidate-human", "plan-7", "record-7"]
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
        plan = self.purge.plan(command_id="purge-plan-partial", plan_id="plan-7", target_refs=[self.claim])
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

    def test_purge_backup_restore_replays_external_ledger_without_resurrection(self):
        self.memory.retain_raw(command_id="retain-for-purge", object_id=self.claim, run_id="run-7")
        result = self._human_verification("verify-for-purge")
        self.memory.create_candidate(command_id="candidate-for-purge", candidate_id="candidate-7", claim_ref=self.claim, evidence_refs=[self.evidence], owner="agent", classification_assertion_ref="class-claim-7", verification_ref=result["verification_id"], review_trigger="review")
        plan = self.purge.plan(command_id="purge-plan-success", plan_id="plan-7", target_refs=[self.claim])
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
        outcome = self.purge.execute(command_id="purge-execute-success", record_id="record-7", barrier_id="barrier-7", plan=plan, grant_id="grant-7", task_id="task-7", approval_id="approval-7")
        self.assertEqual(outcome["status"], "COMPLETED")
        with self.assertRaises(PurgedObject):
            self.store.get_payload(self.claim)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM raw_history_fts WHERE raw_history_fts MATCH 'governed' ").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM admitted_memory_fts WHERE admitted_memory_fts MATCH 'governed' ").fetchone()[0], 0)
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
            self.assertEqual(restored_memory.search_raw(query="governed memory", run_id="run-7"), [])
            self.assertEqual(restored_memory.search_admitted(query="governed memory", run_id="run-7"), [])
            with restored._connection() as conn:
                self.assertEqual(conn.execute("SELECT status FROM purge_barriers WHERE barrier_id='barrier-7'").fetchone()[0], "RELEASED")
                self.assertEqual([row[0] for row in conn.execute("SELECT action FROM purge_ledger ORDER BY ledger_seq")], ["BARRIER_INSTALLED", "BARRIER_RELEASED"])
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
