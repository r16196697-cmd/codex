import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from adapters.storage import ObjectStore
from kernel.authority import AuthorityService
from kernel.authority.errors import AuthorizationDenied
from kernel.object.errors import CommandConflict
from kernel.memory.service import MemoryService
from kernel.purge.service import PurgeService
from kernel.run import TraceAdmissionDenied, TraceRuntime
from kernel.runtime import RuntimeModeService, require_mode_permission
from kernel.runtime.inspect import InspectService
from kernel.runtime.errors import RuntimeDenied


class RuntimeModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nexus-mode-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "data"
        self.store = ObjectStore(self.root)
        self.addCleanup(self.store.close)
        policy_path = Path(__file__).resolve().parents[2] / "policies" / "default-policy.json"
        self.policy = json.loads(policy_path.read_text(encoding="utf-8"))
        self.policy["trust_anchors"] = ["human-root"]
        self.authority = AuthorityService(self.store, self.policy)
        self.trace = TraceRuntime(self.store, self.authority)
        self.authority.register_principal({"schema_id":"nexus.principal","schema_version":1,"principal_id":"human-root","principal_type":"HUMAN","status":"ACTIVE"}, "mode-principal-human")
        self.authority.register_principal({"schema_id":"nexus.principal","schema_version":1,"principal_id":"operator-agent","principal_type":"SERVICE","status":"ACTIVE"}, "mode-principal-agent")
        self.authority.register_trust_anchor({"schema_id":"nexus.trust_anchor","schema_version":1,"anchor_id":"mode-anchor","principal_id":"human-root","policy_ref":"1"}, "mode-anchor")
        now = datetime.now(timezone.utc)
        self.authority.create_grant({"schema_id":"nexus.delegation_grant","schema_version":1,"grant_id":"mode-grant","issued_by":"human-root","granted_to":"operator-agent","task_scope":["mode-task","task-no-root"],"resource_scope":["runtime-mode:instance","mode-root-run"],"action_scope":["RUNTIME_CONFIGURE","RUN_CREATE","TRACE_APPEND"],"audience_scope":["nexus-runtime"],"issued_at":now.isoformat(),"expires_at":(now+timedelta(days=1)).isoformat(),"status":"ACTIVE","policy_version":"1"}, "mode-grant")
        self.trace.create_task({"schema_id":"nexus.task","schema_version":1,"task_id":"mode-task","requester_id":"human-root","status":"CREATED","created_at":now.isoformat(),"command_id":"mode-task-create"})
        boundary = {"allowed_classifications":["PUBLIC"],"handling_tags":[]}
        with self.store._connection() as conn:
            conn.execute("INSERT INTO classification_assertions VALUES(?,?,?,?,?,?,?, ?,NULL)", ("mode-run-class", "RUN", "mode-root-run", "PUBLIC", "[]", "1", "synthetic mode trace test", "operator-agent"))
            conn.execute("INSERT INTO classification_assertions VALUES(?,?,?,?,?,?,?, ?,NULL)", ("mode-root-event-class", "TRACE_EVENT", "evt-mode-root-create", "PUBLIC", "[]", "1", "synthetic mode trace test", "operator-agent"))
        self.trace.create_run({"schema_id":"nexus.run","schema_version":1,"run_id":"mode-root-run","task_id":"mode-task","executor_kind":"ORCHESTRATOR","status":"CREATED","grant_id":"mode-grant","data_boundary":boundary,"classification_assertion_ref":"mode-run-class","created_at":now.isoformat()}, command_id="mode-root-create", event_classification_assertion_ref="mode-root-event-class")
        self.modes = RuntimeModeService(self.store, self.authority)

    def _mode_classification(self, command_id):
        assertion_id = "mode-event-class-" + command_id
        with self.store._connection() as conn:
            conn.execute("INSERT OR IGNORE INTO classification_assertions VALUES(?,?,?,?,?,?,?, ?,NULL)", (assertion_id, "TRACE_EVENT", "evt-" + command_id, "PUBLIC", "[]", "1", "synthetic mode trace test", "operator-agent"))
        return assertion_id

    def set_mode(self, *, command_id, mode):
        return self.modes.set_mode(command_id=command_id, grant_id="mode-grant", task_id="mode-task", mode=mode, classification_assertion_ref=self._mode_classification(command_id))

    def test_initial_normal_mode_and_authorized_idempotent_transition(self):
        self.assertEqual(self.modes.current()["mode"], "NORMAL")
        result = self.set_mode(command_id="mode-safe", mode="SAFE")
        self.assertEqual(result["previous_mode"], "NORMAL")
        self.assertEqual(result["run_id"], "mode-root-run")
        with self.store._connection() as conn:
            event = conn.execute("SELECT event_json FROM trace_events WHERE event_id=?", (result["trace_event_id"],)).fetchone()
        event = json.loads(event["event_json"])
        self.assertEqual(event["event_type"], "nexus.runtime.mode_changed")
        self.assertEqual(event["typed_metadata"], {"previous_mode": "NORMAL", "next_mode": "SAFE"})
        self.assertEqual(self.trace.replay_run("mode-root-run"), {"run_id": "mode-root-run", "status": "CREATED", "last_seq": 2, "event_count": 2})
        self.assertEqual(self.trace.replay_task("mode-task"), {"task_id": "mode-task", "status": "ACTIVE", "root_run_id": "mode-root-run"})
        self.assertEqual(self.modes.current()["mode"], "SAFE")
        self.assertEqual(self.set_mode(command_id="mode-safe", mode="SAFE"), result)

    def test_mode_change_requires_valid_trace_classification_and_root_run(self):
        with self.assertRaises(TraceAdmissionDenied):
            self.modes.set_mode(command_id="mode-bad-class", grant_id="mode-grant", task_id="mode-task", mode="SAFE", classification_assertion_ref="mode-run-class")
        self.assertEqual(self.modes.current()["mode"], "NORMAL")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM runtime_mode_events").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM trace_events WHERE event_type='nexus.runtime.mode_changed'").fetchone()[0], 0)
        self.trace.create_task({"schema_id":"nexus.task","schema_version":1,"task_id":"task-no-root","requester_id":"human-root","status":"CREATED","created_at":datetime.now(timezone.utc).isoformat(),"command_id":"task-no-root-create"})
        no_root_class = self._mode_classification("mode-no-root")
        with self.assertRaisesRegex(RuntimeDenied, "MODE_TRACE_ROOT_RUN_REQUIRED"):
            self.modes.set_mode(command_id="mode-no-root", grant_id="mode-grant", task_id="task-no-root", mode="SAFE", classification_assertion_ref=no_root_class)
        self.assertEqual(self.modes.current()["mode"], "NORMAL")

    def test_mode_change_requires_trace_append_authority(self):
        now = datetime.now(timezone.utc)
        self.authority.create_grant({"schema_id":"nexus.delegation_grant","schema_version":1,"grant_id":"mode-no-trace-grant","issued_by":"human-root","granted_to":"operator-agent","task_scope":["task-no-trace"],"resource_scope":["runtime-mode:instance","no-trace-root"],"action_scope":["RUNTIME_CONFIGURE","RUN_CREATE"],"audience_scope":["nexus-runtime"],"issued_at":now.isoformat(),"expires_at":(now+timedelta(days=1)).isoformat(),"status":"ACTIVE","policy_version":"1"}, "mode-no-trace-grant-create")
        self.trace.create_task({"schema_id":"nexus.task","schema_version":1,"task_id":"task-no-trace","requester_id":"human-root","status":"CREATED","created_at":now.isoformat(),"command_id":"task-no-trace-create"})
        with self.store._connection() as conn:
            conn.execute("INSERT INTO classification_assertions VALUES(?,?,?,?,?,?,?, ?,NULL)", ("no-trace-run-class", "RUN", "no-trace-root", "PUBLIC", "[]", "1", "synthetic no-trace test", "operator-agent"))
            conn.execute("INSERT INTO classification_assertions VALUES(?,?,?,?,?,?,?, ?,NULL)", ("no-trace-root-event-class", "TRACE_EVENT", "evt-no-trace-root-create", "PUBLIC", "[]", "1", "synthetic no-trace test", "operator-agent"))
        self.trace.create_run({"schema_id":"nexus.run","schema_version":1,"run_id":"no-trace-root","task_id":"task-no-trace","executor_kind":"ORCHESTRATOR","status":"CREATED","grant_id":"mode-no-trace-grant","data_boundary":{"allowed_classifications":["PUBLIC"],"handling_tags":[]},"classification_assertion_ref":"no-trace-run-class","created_at":now.isoformat()}, command_id="no-trace-root-create", event_classification_assertion_ref="no-trace-root-event-class")
        assertion = self._mode_classification("mode-no-trace")
        with self.assertRaises(AuthorizationDenied):
            self.modes.set_mode(command_id="mode-no-trace", grant_id="mode-no-trace-grant", task_id="task-no-trace", mode="SAFE", classification_assertion_ref=assertion)
        self.assertEqual(self.modes.current()["mode"], "NORMAL")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM runtime_mode_events WHERE task_id='task-no-trace'").fetchone()[0], 0)

    def test_mode_change_is_authorized_and_command_id_conflict_is_rejected(self):
        with self.assertRaises(AuthorizationDenied):
            self.modes.set_mode(command_id="mode-denied", grant_id="missing-grant", task_id="mode-task", mode="SAFE", classification_assertion_ref="missing")
        self.set_mode(command_id="mode-safe", mode="SAFE")
        with self.assertRaises(CommandConflict):
            self.modes.set_mode(command_id="mode-safe", grant_id="mode-grant", task_id="mode-task", mode="STATELESS", classification_assertion_ref="mode-event-class-mode-safe")

    def test_mode_event_insert_failure_rolls_back_mode_and_command(self):
        with self.store._connection() as conn:
            conn.execute("CREATE TRIGGER test_fail_mode_trace BEFORE INSERT ON trace_events WHEN NEW.event_type='nexus.runtime.mode_changed' BEGIN SELECT RAISE(ABORT,'SIMULATED_MODE_TRACE_FAILURE'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "SIMULATED_MODE_TRACE_FAILURE"):
            self.set_mode(command_id="mode-trace-fail", mode="SAFE")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT mode FROM runtime_mode_state WHERE singleton=1").fetchone()[0], "NORMAL")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM runtime_mode_events WHERE command_id='mode-trace-fail'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM command_ledger WHERE command_id='mode-trace-fail'").fetchone()[0], 0)
            conn.execute("DROP TRIGGER test_fail_mode_trace")
        result = self.set_mode(command_id="mode-trace-fail", mode="SAFE")
        self.assertEqual(self.modes.current()["mode"], "SAFE")
        self.assertEqual(result["mode"], "SAFE")

    def test_mode_persists_when_store_is_reopened(self):
        self.set_mode(command_id="mode-stateless", mode="STATELESS")
        self.store.close()
        self.store = ObjectStore(self.root)
        self.addCleanup(self.store.close)
        self.authority.store = self.store
        self.modes = RuntimeModeService(self.store, self.authority)
        self.assertEqual(self.modes.current()["mode"], "STATELESS")

    def test_non_normal_startup_does_not_migrate_or_clean_payloads(self):
        self.set_mode(command_id="mode-safe", mode="SAFE")
        orphan = self.store.blob_root / "aa" / "startup-orphan"
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_bytes(b"non-normal-startup-must-not-clean")
        self.store.close()
        self.store = ObjectStore(self.root)
        self.addCleanup(self.store.close)
        self.authority.store = self.store
        self.modes = RuntimeModeService(self.store, self.authority)
        self.assertEqual(self.modes.current()["mode"], "SAFE")
        self.assertTrue(orphan.exists())

    def test_four_mode_policy_matrix_fails_closed(self):
        require_mode_permission("NORMAL", "memory_read")
        require_mode_permission("NORMAL", "memory_write")
        require_mode_permission("SAFE", "memory_read")
        require_mode_permission("SAFE", "effect_commit", effect_class="READ_ONLY")
        require_mode_permission("STATELESS", "trace_write", event_type="nexus.run.transitioned")
        require_mode_permission("STATELESS", "effect_commit", effect_class="LOCAL_MUTATION")
        with self.assertRaises(RuntimeDenied): require_mode_permission("SAFE", "memory_write")
        with self.assertRaises(RuntimeDenied): require_mode_permission("STATELESS", "memory_read")
        with self.assertRaises(RuntimeDenied): require_mode_permission("STATELESS", "memory_write")
        with self.assertRaises(RuntimeDenied): require_mode_permission("STATELESS", "trace_write", event_type="nexus.trace.payload_dumped")
        with self.assertRaises(RuntimeDenied): require_mode_permission("SAFE", "effect_commit", effect_class="EXTERNAL_REVERSIBLE")
        with self.assertRaises(RuntimeDenied): require_mode_permission("SAFE", "egress")
        with self.assertRaises(RuntimeDenied): require_mode_permission("RECOVERY", "core_read")
        with self.assertRaises(RuntimeDenied): require_mode_permission("RECOVERY", "core_write")

    def test_recovery_mode_cannot_be_exited_by_ordinary_set_mode(self):
        self.set_mode(command_id="mode-recovery", mode="RECOVERY")
        with self.assertRaises(RuntimeDenied):
            self.modes.set_mode(command_id="mode-normal", grant_id="mode-grant", task_id="mode-task", mode="NORMAL", classification_assertion_ref="unused-in-recovery")

    def test_safe_and_stateless_are_enforced_at_memory_trace_and_egress_apis(self):
        memory = MemoryService(self.store, self.authority, verifier=None)
        self.set_mode(command_id="mode-safe", mode="SAFE")
        with self.assertRaisesRegex(RuntimeDenied, "RUNTIME_MODE_DENIED"):
            memory.retain_raw(command_id="safe-retain", object_id="missing", run_id="missing")
        with self.assertRaisesRegex(RuntimeDenied, "RUNTIME_MODE_DENIED"):
            self.authority.evaluate_egress("remote-destination", ["missing"])
        with self.assertRaises(TraceAdmissionDenied):
            self.trace.append_trace_event(command_id="safe-trace", run_id="missing", event_type="nexus.object.created", classification_assertion_ref="missing", typed_metadata={"object_type":"artifact"})

        from kernel.budget import BudgetService
        from kernel.runtime import DeterministicRuntime
        deterministic = DeterministicRuntime(self.store, self.authority, BudgetService(self.store), self.trace)
        with self.assertRaisesRegex(RuntimeDenied, "RUNTIME_MODE_DENIED"):
            deterministic.register_model_profile(command_id="safe-profile", grant_id="mode-grant", task_id="mode-task", profile={})
        with self.assertRaisesRegex(RuntimeDenied, "RUNTIME_MODE_DENIED"):
            deterministic.register_tool_descriptor(command_id="safe-descriptor", grant_id="mode-grant", task_id="mode-task", descriptor={})
        with self.store._connection() as conn:
            conn.execute("SELECT count(*) FROM raw_history_rows").fetchone()
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("UPDATE raw_history_rows SET expires_at=expires_at WHERE 0")

        self.set_mode(command_id="mode-stateless", mode="STATELESS")
        with self.assertRaisesRegex(RuntimeDenied, "RUNTIME_MODE_DENIED"):
            memory.search_raw(query="anything", run_id="missing")
        with self.assertRaisesRegex(RuntimeDenied, "RUNTIME_MODE_DENIED"):
            memory.create_candidate(command_id="stateless-candidate", candidate_id="candidate", claim_ref="claim", evidence_refs=[], owner="operator", classification_assertion_ref="class", verification_ref="verification", review_trigger="manual")
        with self.assertRaisesRegex(RuntimeDenied, "RUNTIME_MODE_DENIED"):
            self.trace.append_trace_event(command_id="stateless-trace-denied", run_id="missing", event_type="nexus.unapproved.payload", classification_assertion_ref="missing", typed_metadata={})

        with self.assertRaisesRegex(RuntimeDenied, "RUNTIME_MODE_DENIED"):
            deterministic.register_model_profile(command_id="stateless-profile", grant_id="mode-grant", task_id="mode-task", profile={})
        with self.assertRaisesRegex(RuntimeDenied, "RUNTIME_MODE_DENIED"):
            deterministic.register_tool_descriptor(command_id="stateless-descriptor", grant_id="mode-grant", task_id="mode-task", descriptor={})
        with self.store._connection() as conn:
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("SELECT count(*) FROM raw_history_rows").fetchone()
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("UPDATE raw_history_rows SET expires_at=expires_at WHERE 0")

    def test_inspect_requires_exact_authority_and_returns_bounded_read_only_projection(self):
        now = datetime.now(timezone.utc)
        self.authority.create_grant({
            "schema_id":"nexus.delegation_grant","schema_version":1,"grant_id":"inspect-grant",
            "issued_by":"human-root","granted_to":"operator-agent","task_scope":["mode-task"],
            "resource_scope":["task:mode-task"],"action_scope":["INSPECT"],
            "audience_scope":["nexus-runtime","nexus-inspect"],"issued_at":now.isoformat(),
            "expires_at":(now+timedelta(days=1)).isoformat(),"status":"ACTIVE","policy_version":"1"
        }, "inspect-grant-create")
        inspector = InspectService(self.store, self.authority)
        before = self.store._request_hash("inspect-test", {"mode": self.modes.current()["mode"]})
        view = inspector.task(grant_id="inspect-grant", task_id="mode-task")
        after = self.store._request_hash("inspect-test", {"mode": self.modes.current()["mode"]})
        self.assertEqual(before, after)
        self.assertEqual(view["task"]["task_id"], "mode-task")
        self.assertEqual(view["runs"][0]["executor_kind"], "ORCHESTRATOR")
        with self.assertRaises(AuthorizationDenied):
            inspector.task(grant_id="mode-grant", task_id="mode-task")

    def test_inspect_api_is_closed_in_recovery_mode(self):
        self.set_mode(command_id="mode-recovery", mode="RECOVERY")
        with self.assertRaisesRegex(RuntimeDenied, "RUNTIME_RECOVERY_CORE_BYPASS"):
            InspectService(self.store, self.authority).task(grant_id="mode-grant", task_id="mode-task")

    def test_recovery_only_returns_to_normal_after_validation(self):
        with self.store._connection() as conn:
            conn.execute("INSERT INTO classification_assertions VALUES(?,?,?,?,?,?,?, ?,NULL)", ("recovery-object-class", "OBJECT", "recovery-object", "PUBLIC", "[]", "1", "synthetic recovery check", "operator-agent"))
        self.store.put_object(command_id="recovery-object-put", object_id="recovery-object", payload=b"recovery sha256 validation", object_type="artifact", created_by_run="unbound-test-run", classification_assertion_ref="recovery-object-class")
        self.set_mode(command_id="mode-recovery", mode="RECOVERY")
        memory = MemoryService(self.store, self.authority, verifier=None)
        purge = PurgeService(self.store, self.authority, memory, independent_journal_path=Path(self.temp.name) / "purge-ledger.jsonl")
        report = self.modes.complete_validated_recovery(command_id="validated-recovery", purge_service=purge)
        self.assertEqual(report["mode"], "NORMAL")
        self.assertEqual(report["validated_objects"], 1)
        self.assertEqual(self.modes.current()["mode"], "NORMAL")
        self.assertEqual(self.modes.complete_validated_recovery(command_id="validated-recovery", purge_service=purge), report)

    def test_recovery_keeps_isolation_when_purge_journal_has_pending_barrier(self):
        self.set_mode(command_id="mode-recovery", mode="RECOVERY")
        memory = MemoryService(self.store, self.authority, verifier=None)
        purge = PurgeService(self.store, self.authority, memory, independent_journal_path=Path(self.temp.name) / "purge-ledger.jsonl")
        purge.journal.append(action="BARRIER_INSTALLED", barrier_id="restore-barrier", plan_id="restore-plan", plan_hash="a" * 64, lineage_revision=0, protected_refs=[])
        with self.assertRaisesRegex(RuntimeDenied, "RECOVERY_PURGE_BARRIER_OR_LEDGER_UNRESOLVED"):
            self.modes.complete_validated_recovery(command_id="validated-recovery-blocked", purge_service=purge)
        self.assertEqual(self.modes.current()["mode"], "RECOVERY")

    def test_recovery_keeps_isolation_when_payload_integrity_fails(self):
        with self.store._connection() as conn:
            conn.execute("INSERT INTO classification_assertions VALUES(?,?,?,?,?,?,?, ?,NULL)", ("recovery-corrupt-class", "OBJECT", "recovery-corrupt", "PUBLIC", "[]", "1", "synthetic recovery check", "operator-agent"))
        self.store.put_object(command_id="recovery-corrupt-put", object_id="recovery-corrupt", payload=b"original-recovery-payload", object_type="artifact", created_by_run="unbound-test-run", classification_assertion_ref="recovery-corrupt-class")
        metadata = self.store.get_object_metadata("recovery-corrupt")
        payload_path = self.store._payload_path(metadata["payload_uri"])
        payload_path.write_bytes(b"modified-recovery-payload")
        self.set_mode(command_id="mode-recovery", mode="RECOVERY")
        memory = MemoryService(self.store, self.authority, verifier=None)
        purge = PurgeService(self.store, self.authority, memory, independent_journal_path=Path(self.temp.name) / "purge-ledger.jsonl")
        with self.assertRaisesRegex(RuntimeDenied, "RECOVERY_OBJECT_INTEGRITY_VALIDATION_FAILED"):
            self.modes.complete_validated_recovery(command_id="validated-recovery-corrupt", purge_service=purge)
        self.assertEqual(self.modes.current()["mode"], "RECOVERY")

    def test_recovery_blocks_object_and_trace_apis(self):
        self.set_mode(command_id="mode-recovery", mode="RECOVERY")
        orphan = self.store.blob_root / "ff" / "orphan-payload"
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_bytes(b"recovery-must-not-clean")
        self.store.close()
        self.store = ObjectStore(self.root)
        self.addCleanup(self.store.close)
        self.authority.store = self.store
        self.trace = TraceRuntime(self.store, self.authority)
        self.modes = RuntimeModeService(self.store, self.authority)
        self.assertEqual(self.modes.current()["mode"], "RECOVERY")
        self.assertTrue(orphan.exists(), "Recovery startup must not run orphan payload cleanup")
        with self.assertRaisesRegex(RuntimeDenied, "RUNTIME_RECOVERY_CORE_BYPASS"):
            self.store.get_object_metadata("missing")
        with self.assertRaisesRegex(RuntimeDenied, "RUNTIME_RECOVERY_CORE_BYPASS"):
            self.store.put_object(command_id="recovery-object", object_id="blocked", payload=b"x", object_type="artifact", created_by_run="missing", classification_assertion_ref="missing")
        with self.assertRaisesRegex(RuntimeDenied, "RUNTIME_RECOVERY_CORE_BYPASS"):
            self.trace.create_task({"schema_id":"nexus.task","schema_version":1,"task_id":"recovery-task","requester_id":"human-root","status":"CREATED","created_at":datetime.now(timezone.utc).isoformat(),"command_id":"recovery-task-create"})


if __name__ == "__main__":
    unittest.main()
