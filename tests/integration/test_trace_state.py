import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from adapters.storage import ObjectStore
from kernel.authority import AuthorityService
from kernel.run import InvalidRunTransition, TraceAdmissionDenied, TraceRuntime


class TraceStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nexus-step4-")
        self.store = ObjectStore(Path(self.temp.name) / "data")
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.store.close)
        self.policy = json.loads((Path(__file__).resolve().parents[2] / "policies" / "default-policy.json").read_text(encoding="utf-8"))
        self.policy["trust_anchors"] = ["human-root"]
        self.authority = AuthorityService(self.store, self.policy)
        self.runtime = TraceRuntime(self.store, self.authority)
        self._principal("human-root", "HUMAN")
        self._principal("agent", "SERVICE")
        self.authority.register_trust_anchor({"schema_id": "nexus.trust_anchor", "schema_version": 1, "anchor_id": "anchor-1", "principal_id": "human-root", "policy_ref": "1"}, "cmd-anchor")
        self._grant()
        self.runtime.create_task({"schema_id": "nexus.task", "schema_version": 1, "task_id": "task-1", "requester_id": "human-root", "status": "CREATED", "created_at": self._now(), "command_id": "cmd-task"})
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
        grant = {"schema_id": "nexus.delegation_grant", "schema_version": 1, "grant_id": "grant-root", "issued_by": "human-root", "granted_to": "agent", "task_scope": ["task-1"], "resource_scope": ["run-1"], "action_scope": ["RUN_CREATE", "RUN_TRANSITION", "TRACE_APPEND", "DELEGATE"], "audience_scope": ["nexus-runtime"], "issued_at": now.isoformat(), "expires_at": (now.replace(year=now.year + 1)).isoformat(), "status": "ACTIVE", "policy_version": "1"}
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

    def test_transition_retry_returns_recorded_result_before_stale_state_check(self):
        self.assertEqual(self._create_run()["seq_no"], 1)
        ready_class = self._event_classification("cmd-ready")
        result = self.runtime.transition_run(command_id="cmd-ready", run_id="run-1", expected_state="CREATED", next_state="READY", classification_assertion_ref=ready_class)
        self.assertEqual(result["seq_no"], 2)
        self.store.close()
        self.store = ObjectStore(Path(self.temp.name) / "data")
        self.addCleanup(self.store.close)
        self.authority = AuthorityService(self.store, self.policy)
        self.runtime = TraceRuntime(self.store, self.authority)
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

    def test_state_and_trace_rollback_together_when_event_insert_fails(self):
        self._create_run()
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
        ready_class = self._event_classification("cmd-ready")
        self.runtime.transition_run(command_id="cmd-ready", run_id="run-1", expected_state="CREATED", next_state="READY", classification_assertion_ref=ready_class)
        self.store.close()
        self.store = ObjectStore(Path(self.temp.name) / "data")
        self.addCleanup(self.store.close)
        self.authority = AuthorityService(self.store, self.policy)
        self.runtime = TraceRuntime(self.store, self.authority)
        self.assertEqual(self.runtime.replay_run("run-1")["status"], "READY")

    def _event_count(self):
        with self.store._connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM trace_events WHERE run_id='run-1'").fetchone()[0]

    def _read_event(self, event_id):
        with self.store._connection() as conn:
            return json.loads(conn.execute("SELECT event_json FROM trace_events WHERE event_id=?", (event_id,)).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
