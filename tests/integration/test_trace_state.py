import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from adapters.storage import ObjectStore
from kernel.authority import AuthorityService
from kernel.budget import BudgetService
from kernel.run import InvalidRunTransition, TraceAdmissionDenied, TraceRuntime
from kernel.runtime import DeterministicRuntime


class TraceStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nexus-step4-")
        self.store = ObjectStore(Path(self.temp.name) / "data")
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.store.close)
        self.policy = json.loads((Path(__file__).resolve().parents[2] / "policies" / "default-policy.json").read_text(encoding="utf-8"))
        self.policy["trust_anchors"] = ["human-root"]
        self.authority = AuthorityService(self.store, self.policy)
        self.budget = BudgetService(self.store)
        self.runtime = TraceRuntime(self.store, self.authority)
        self.deterministic = DeterministicRuntime(self.store, self.authority, self.budget, self.runtime)
        self._principal("human-root", "HUMAN")
        self._principal("agent", "SERVICE")
        self.authority.register_trust_anchor({"schema_id": "nexus.trust_anchor", "schema_version": 1, "anchor_id": "anchor-1", "principal_id": "human-root", "policy_ref": "1"}, "cmd-anchor")
        self._grant()
        self.runtime.create_task({"schema_id": "nexus.task", "schema_version": 1, "task_id": "task-1", "requester_id": "human-root", "status": "CREATED", "created_at": self._now(), "command_id": "cmd-task"})
        self.budget.create_account(command_id="cmd-budget-account", account_id="budget-task-1", task_id="task-1", amount_limit=100, unit="credits", model_call_limit=10, tool_call_limit=10, child_run_limit=10)
        self._classify("class-run-1", "RUN", "run-1")
        self._classify("class-event-created", "TRACE_EVENT", "evt-cmd-create-run")
        self.run_event_class_ref = "class-event-created"
        self.run = {"schema_id": "nexus.run", "schema_version": 1, "run_id": "run-1", "task_id": "task-1", "executor_kind": "ORCHESTRATOR", "status": "CREATED", "grant_id": "grant-root", "data_boundary": {"allowed_classifications": ["PUBLIC"], "handling_tags": []}, "classification_assertion_ref": "class-run-1", "created_at": self._now()}

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    def _principal(self, principal_id, principal_type):
        self.authority.register_principal({"schema_id": "nexus.principal", "schema_version": 1, "principal_id": principal_id, "principal_type": principal_type, "status": "ACTIVE"}, f"cmd-principal-{principal_id}")

    def _grant(self):
        now = datetime.now(timezone.utc)
        grant = {"schema_id": "nexus.delegation_grant", "schema_version": 1, "grant_id": "grant-root", "issued_by": "human-root", "granted_to": "agent", "task_scope": ["task-1"], "resource_scope": ["run-1"], "action_scope": ["RUN_CREATE", "RUN_TRANSITION", "TRACE_APPEND", "DELEGATE", "OBJECT_WRITE"], "audience_scope": ["nexus-runtime"], "issued_at": now.isoformat(), "expires_at": (now.replace(year=now.year + 1)).isoformat(), "status": "ACTIVE", "policy_version": "1"}
        self.authority.create_grant(grant, "cmd-grant")

    def _classify(self, assertion_id, subject_type, subject_ref, level="PUBLIC", tags=(), actor="agent"):
        with self.store._connection() as conn:
            conn.execute("INSERT INTO classification_assertions(assertion_id,subject_type,subject_ref,sensitivity_level,handling_tags_json,policy_version,reason,actor_id) VALUES(?,?,?,?,?,?,?,?)", (assertion_id, subject_type, subject_ref, level, json.dumps(list(tags)), "1", "synthetic test assertion", actor))

    def _event_classification(self, command_id, *, level="PUBLIC", tags=()):
        event_id = "evt-" + command_id
        assertion_id = "class-" + event_id
        self._classify(assertion_id, "TRACE_EVENT", event_id, level, tags)
        return assertion_id

    def _create_run(self):
        return self.runtime.create_run(self.run, command_id="cmd-create-run", event_classification_assertion_ref=self.run_event_class_ref)

    def _prepare_manifest(self):
        contract_id = "contract-task-1"
        self._classify("class-contract-task-1", "OBJECT", contract_id)
        contract = {"schema_id": "nexus.task_contract", "schema_version": 1, "task_id": "task-1", "requester_id": "human-root", "goal": "test goal", "constraints": [], "success_criteria": ["pass"], "risk_class": "STANDARD", "budget_account_ref": "budget-task-1", "routing_constraints": {"allowed_providers": [], "forbidden_providers": [], "locality": "ANY", "network_required": False, "modalities": ["text"]}, "routing_preferences": {"optimize_for": "BALANCED"}, "created_at": self._now()}
        self.deterministic.bind_task_contract(command_id="cmd-bind-contract", root_run_id="run-1", contract_object_id=contract_id, classification_assertion_ref="class-contract-task-1", contract=contract)
        self.deterministic.create_dag(command_id="cmd-empty-dag", task_id="task-1", root_run_id="run-1", nodes=[])
        manifest_id = "manifest-run-1"
        self._classify("class-manifest-run-1", "OBJECT", manifest_id)
        manifest = {"schema_id": "nexus.run_manifest", "schema_version": 1, "executor_kind": "ORCHESTRATOR", "runtime_version": "0.1", "policy_version": "1", "schema_versions": {"nexus.run_manifest": 1}, "input_object_refs": [], "authority_grant_ref": "grant-root", "data_boundary": self.run["data_boundary"], "classification_assertion_ref": self.run["classification_assertion_ref"], "task_contract_ref": contract_id, "dag_version": "1", "scheduler_version": "1"}
        self.deterministic.bind_manifest(command_id="cmd-bind-manifest", run_id="run-1", manifest_object_id=manifest_id, manifest_classification_assertion_ref="class-manifest-run-1", manifest=manifest)

    def test_transition_retry_returns_recorded_result_before_stale_state_check(self):
        self.assertEqual(self._create_run()["seq_no"], 1)
        before = self._event_count()
        self.runtime.create_run(self.run, command_id="cmd-create-run", event_classification_assertion_ref=self.run_event_class_ref)
        self.assertEqual(self._event_count(), before)
        self._prepare_manifest()
        ready_class = self._event_classification("cmd-ready")
        result = self.runtime.transition_run(command_id="cmd-ready", run_id="run-1", expected_state="CREATED", next_state="READY", classification_assertion_ref=ready_class)
        self.assertEqual(result["seq_no"], 2)
        before_retry = self._event_count()
        self.store.close()
        self.store = ObjectStore(Path(self.temp.name) / "data")
        self.addCleanup(self.store.close)
        self.authority = AuthorityService(self.store, self.policy)
        self.runtime = TraceRuntime(self.store, self.authority)
        with self.store._connection() as conn:
            created_at = conn.execute("SELECT created_at FROM tasks WHERE task_id='task-1'").fetchone()[0]
        self.runtime.create_task({"schema_id": "nexus.task", "schema_version": 1, "task_id": "task-1", "requester_id": "human-root", "status": "CREATED", "created_at": created_at, "command_id": "cmd-task"})
        self.assertEqual(self._event_count(), before_retry)
        self.runtime.create_run(self.run, command_id="cmd-create-run", event_classification_assertion_ref=self.run_event_class_ref)
        self.assertEqual(self._event_count(), before_retry)
        self.assertEqual(self.runtime.transition_run(command_id="cmd-ready", run_id="run-1", expected_state="CREATED", next_state="READY", classification_assertion_ref=ready_class), result)
        stale_class = self._event_classification("cmd-stale")
        with self.assertRaises(InvalidRunTransition):
            self.runtime.transition_run(command_id="cmd-stale", run_id="run-1", expected_state="CREATED", next_state="RUNNING", classification_assertion_ref=stale_class)
        self.assertEqual(self.runtime.replay_run("run-1"), {"run_id": "run-1", "status": "READY", "last_seq": 2, "event_count": 2})
        waiting_class = self._event_classification("cmd-running")
        self.runtime.transition_run(command_id="cmd-running", run_id="run-1", expected_state="READY", next_state="RUNNING", classification_assertion_ref=waiting_class)
        paused_class = self._event_classification("cmd-waiting")
        self.runtime.transition_run(command_id="cmd-waiting", run_id="run-1", expected_state="RUNNING", next_state="WAITING", classification_assertion_ref=paused_class)
        self.assertEqual(self.runtime.replay_task("task-1"), {"task_id": "task-1", "status": "WAITING", "root_run_id": "run-1"})

    def test_run_cannot_enter_ready_without_bound_manifest(self):
        self._create_run()
        classification = self._event_classification("cmd-ready-without-manifest")
        with self.assertRaisesRegex(TraceAdmissionDenied, "RUN_READY_REQUIRES_BOUND_MANIFEST"):
            self.runtime.transition_run(command_id="cmd-ready-without-manifest", run_id="run-1", expected_state="CREATED", next_state="READY", classification_assertion_ref=classification)
        self.assertEqual(self.runtime.replay_run("run-1")["status"], "CREATED")
        self.assertEqual(self._event_count(), 1)

    def test_state_and_trace_rollback_together_when_event_insert_fails(self):
        self._create_run()
        self._prepare_manifest()
        assertion = self._event_classification("cmd-fail-mid-commit")
        with self.store._connection() as conn:
            conn.execute("CREATE TRIGGER test_fail_trace_insert BEFORE INSERT ON trace_events WHEN NEW.event_type='nexus.run.transitioned' BEGIN SELECT RAISE(ABORT,'SIMULATED_CRASH'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.runtime.transition_run(command_id="cmd-fail-mid-commit", run_id="run-1", expected_state="CREATED", next_state="READY", classification_assertion_ref=assertion)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM runs WHERE run_id='run-1'").fetchone()[0], "CREATED")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM trace_events WHERE run_id='run-1'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM command_ledger WHERE command_id='cmd-fail-mid-commit'").fetchone()[0], 0)
            conn.execute("DROP TRIGGER test_fail_trace_insert")
        result = self.runtime.transition_run(command_id="cmd-fail-mid-commit", run_id="run-1", expected_state="CREATED", next_state="READY", classification_assertion_ref=assertion)
        self.assertEqual(result["status"], "READY")

    def test_trace_allowlist_secret_and_payload_guards_reject_without_writes(self):
        self._create_run()
        object_id = "object-guard-fixture"
        object_class = "class-object-guard-fixture"
        self._classify(object_class, "OBJECT", object_id)
        self.store.put_object(command_id="cmd-object-guard-fixture", object_id=object_id, payload=b"fixture", object_type="artifact", created_by_run="run-1", classification_assertion_ref=object_class)
        before = self._event_count()
        cases = [
            ("cmd-unknown", "nexus.unrecognized.event", {"object_type": "artifact"}),
            ("cmd-forged-transition", "nexus.run.transitioned", {"from_status": "CREATED", "to_status": "READY"}),
            ("cmd-payload", "nexus.object.created", {"output_preview": "long user content"}),
            ("cmd-secret", "nexus.object.created", {"object_type": "api_key=abcdefghijk"}),
            ("cmd-composite", "nexus.object.created", {"object_type": ["nested"]}),
        ]
        for command_id, event_type, metadata in cases:
            with self.subTest(event_type=event_type, command_id=command_id):
                assertion = self._event_classification(command_id)
                with self.assertRaises(TraceAdmissionDenied):
                    self.runtime.append_trace_event(command_id=command_id, run_id="run-1", event_type=event_type, classification_assertion_ref=assertion, typed_metadata=metadata, object_refs=[object_id])
        actor_assertion = self._event_classification("cmd-wrong-actor")
        with self.assertRaises(TraceAdmissionDenied):
            self.runtime.append_trace_event(command_id="cmd-wrong-actor", run_id="run-1", event_type="nexus.object.created", classification_assertion_ref=actor_assertion, typed_metadata={"object_type": "artifact"}, object_refs=[object_id], actor_id="human-root")
        self.assertEqual(self._event_count(), before)

    def test_trace_references_objects_and_never_copies_payload(self):
        self._create_run()
        object_id = "object-1"
        object_class = "class-object-1"
        self._classify(object_class, "OBJECT", object_id)
        self.store.put_object(command_id="cmd-object", object_id=object_id, payload=b"binary\x00document", object_type="artifact", created_by_run="run-1", classification_assertion_ref=object_class)
        event_class = self._event_classification("cmd-object-event")
        result = self.runtime.append_trace_event(command_id="cmd-object-event", run_id="run-1", event_type="nexus.object.created", classification_assertion_ref=event_class, typed_metadata={"object_type": "artifact"}, object_refs=[object_id])
        event = self._read_event(result["event_id"])
        self.assertEqual(event["object_refs"], [object_id])
        self.assertNotIn("payload", json.dumps(event).lower())
        self.assertEqual(event["typed_metadata"], {"object_type": "artifact"})

    def test_high_classified_object_cannot_be_downgraded_into_trace(self):
        self._classify("class-run-secret", "RUN", "run-1", "SECRET", ["NO_EXTERNAL_EGRESS"])
        self._classify("class-create-secret", "TRACE_EVENT", "evt-cmd-create-run", "SECRET", ["NO_EXTERNAL_EGRESS"])
        self.run_event_class_ref = "class-create-secret"
        self.run["classification_assertion_ref"] = "class-run-secret"
        self.run["data_boundary"] = {"allowed_classifications": ["PUBLIC", "SECRET"], "handling_tags": ["NO_EXTERNAL_EGRESS"]}
        self._create_run()
        object_id = "object-secret"
        self._classify("class-object-secret", "OBJECT", object_id, "SECRET", ["NO_EXTERNAL_EGRESS"])
        self.store.put_object(command_id="cmd-secret-object", object_id=object_id, payload=b"secret", object_type="evidence", created_by_run="run-1", classification_assertion_ref="class-object-secret")
        low_event_class = self._event_classification("cmd-trace-low")
        with self.assertRaises(TraceAdmissionDenied):
            self.runtime.append_trace_event(command_id="cmd-trace-low", run_id="run-1", event_type="nexus.object.created", classification_assertion_ref=low_event_class, typed_metadata={"object_type": "evidence"}, object_refs=[object_id])

    def test_reopen_replay_reconstructs_run_projection(self):
        self._create_run()
        self._prepare_manifest()
        ready_class = self._event_classification("cmd-ready")
        self.runtime.transition_run(command_id="cmd-ready", run_id="run-1", expected_state="CREATED", next_state="READY", classification_assertion_ref=ready_class)
        self.store.close()
        self.store = ObjectStore(Path(self.temp.name) / "data")
        self.addCleanup(self.store.close)
        self.authority = AuthorityService(self.store, self.policy)
        self.runtime = TraceRuntime(self.store, self.authority)
        self.budget = BudgetService(self.store)
        self.deterministic = DeterministicRuntime(self.store, self.authority, self.budget, self.runtime)
        self.assertEqual(self.runtime.replay_run("run-1")["status"], "READY")

    def test_child_run_validates_task_bound_budget_before_persisting_run(self):
        self._create_run()
        self.runtime.create_task({"schema_id":"nexus.task","schema_version":1,"task_id":"task-b","requester_id":"human-root","status":"CREATED","created_at":self._now(),"command_id":"task-b-create"})
        self.budget.create_account(command_id="budget-b-create", account_id="budget-b", task_id="task-b", amount_limit=100, unit="credits", model_call_limit=10, tool_call_limit=10, child_run_limit=10)
        now = datetime.now(timezone.utc)
        grant = {"schema_id":"nexus.delegation_grant","schema_version":1,"grant_id":"grant-task-b","issued_by":"human-root","granted_to":"agent","task_scope":["task-b"],"resource_scope":["parent-b","child-cross-task","child-run-mismatch","child-released","child-consumed","child-invalid-dimension","child-valid","child-tool-valid"],"action_scope":["RUN_CREATE","TRACE_APPEND"],"audience_scope":["nexus-runtime"],"issued_at":now.isoformat(),"expires_at":(now.replace(year=now.year+1)).isoformat(),"status":"ACTIVE","policy_version":"1"}
        self.authority.create_grant(grant, "grant-task-b-create")
        self._classify("class-parent-b", "RUN", "parent-b")
        self._classify("class-parent-b-event", "TRACE_EVENT", "evt-parent-b-create")
        parent = {"schema_id":"nexus.run","schema_version":1,"run_id":"parent-b","task_id":"task-b","executor_kind":"ORCHESTRATOR","status":"CREATED","grant_id":"grant-task-b","data_boundary":{"allowed_classifications":["PUBLIC"],"handling_tags":[]},"classification_assertion_ref":"class-parent-b","created_at":self._now()}
        self.runtime.create_run(parent, command_id="parent-b-create", event_classification_assertion_ref="class-parent-b-event")

        wrong_task = self.budget.reserve(command_id="reserve-child-cross-task", account_id="budget-task-1", task_id="task-1", run_id="child-cross-task", amount=1, model_calls=1, child_runs=1)
        wrong_run = self.budget.reserve(command_id="reserve-child-wrong-run", account_id="budget-b", task_id="task-b", run_id="different-run", amount=1, model_calls=1, child_runs=1)
        released = self.budget.reserve(command_id="reserve-child-released", account_id="budget-b", task_id="task-b", run_id="child-released", amount=1, model_calls=1, child_runs=1)
        self.budget.release(command_id="release-child-reservation", reservation_id=released)
        consumed = self.budget.reserve(command_id="reserve-child-consumed", account_id="budget-b", task_id="task-b", run_id="child-consumed", amount=1, model_calls=1, child_runs=1)
        self.budget.settle(command_id="consume-child-reservation", reservation_id=consumed, actual_amount=1)
        wrong_dimension = self.budget.reserve(command_id="reserve-child-invalid-dimension", account_id="budget-b", task_id="task-b", run_id="child-invalid-dimension", amount=1, tool_calls=1, child_runs=1)

        child_docs = {}
        def create_child(run_id, reservation_id, command_id, executor_kind="MODEL"):
            with self.store._connection() as conn:
                run_class_exists = conn.execute("SELECT 1 FROM classification_assertions WHERE assertion_id=?", ("class-" + run_id,)).fetchone()
                event_class_exists = conn.execute("SELECT 1 FROM classification_assertions WHERE assertion_id=?", ("class-event-" + run_id,)).fetchone()
            if not run_class_exists:
                self._classify("class-" + run_id, "RUN", run_id)
            if not event_class_exists:
                self._classify("class-event-" + run_id, "TRACE_EVENT", "evt-" + command_id)
            child = child_docs.get(run_id)
            if child is None:
                child = {"schema_id":"nexus.run","schema_version":1,"run_id":run_id,"task_id":"task-b","subtask_id":"sub-"+run_id,"parent_run_id":"parent-b","executor_kind":executor_kind,"status":"CREATED","grant_id":"grant-task-b","budget_reservation_ref":reservation_id,"data_boundary":{"allowed_classifications":["PUBLIC"],"handling_tags":[]},"classification_assertion_ref":"class-"+run_id,"created_at":self._now()}
                child_docs[run_id] = child
            return self.runtime.create_run(child, command_id=command_id, event_classification_assertion_ref="class-event-"+run_id)

        for run_id, reservation_id, command_id in (
            ("child-cross-task", wrong_task, "create-child-cross-task"),
            ("child-run-mismatch", wrong_run, "create-child-run-mismatch"),
            ("child-released", released, "create-child-released"),
            ("child-consumed", consumed, "create-child-consumed"),
            ("child-invalid-dimension", wrong_dimension, "create-child-invalid-dimension"),
        ):
            with self.assertRaisesRegex(TraceAdmissionDenied, "CHILD_RUN_BUDGET_RESERVATION_INVALID"):
                create_child(run_id, reservation_id, command_id)
            with self.store._connection() as conn:
                self.assertIsNone(conn.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone())
                self.assertIsNone(conn.execute("SELECT 1 FROM trace_events WHERE run_id=?", (run_id,)).fetchone())
                self.assertIsNone(conn.execute("SELECT 1 FROM command_ledger WHERE command_id=?", (command_id,)).fetchone())
        correct = self.budget.reserve(command_id="reserve-child-valid", account_id="budget-b", task_id="task-b", run_id="child-valid", amount=1, model_calls=1, child_runs=1)
        result = create_child("child-valid", correct, "create-child-valid")
        self.assertEqual(result["status"], "CREATED")
        self.assertEqual(create_child("child-valid", correct, "create-child-valid"), result)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM trace_events WHERE run_id='child-valid'").fetchone()[0], 1)
        tool_reservation = self.budget.reserve(command_id="reserve-child-tool-valid", account_id="budget-b", task_id="task-b", run_id="child-tool-valid", amount=1, tool_calls=1, child_runs=1)
        tool_run = create_child("child-tool-valid", tool_reservation, "create-child-tool-valid", executor_kind="TOOL")
        self.assertEqual(tool_run["status"], "CREATED")

    def _event_count(self):
        with self.store._connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM trace_events WHERE run_id='run-1'").fetchone()[0]

    def _read_event(self, event_id):
        with self.store._connection() as conn:
            return json.loads(conn.execute("SELECT event_json FROM trace_events WHERE event_id=?", (event_id,)).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
