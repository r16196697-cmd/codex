from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from adapters.client.hosted import CodexHostedBridge
from adapters.panel.application import open_panel_application
from adapters.storage import ObjectStore
from kernel.authority import AuthorityService
from kernel.participation import ParticipationModeService
from kernel.run import TraceRuntime
from kernel.runtime import RuntimeModeService
from kernel.runtime.errors import RuntimeDenied


class ParticipationPanelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nexus-panel-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "data"
        self.store = ObjectStore(self.root)
        self.addCleanup(self.store.close)
        self.participation = ParticipationModeService(self.store)

    def _external_execute(self, sql: str, parameters=()) -> None:
        conn = sqlite3.connect(self.store.database_path)
        try:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute(sql, parameters)
            conn.commit()
        finally:
            conn.close()

    def test_modes_persist_are_orthogonal_and_bypass_does_not_purge(self):
        self.assertEqual(self.participation.current()["mode"], "ACTIVE")
        with self.store._connection() as conn:
            before = tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                           for table in ("objects", "tasks", "runs", "memory_candidates"))
        observed = self.participation.set_mode(mode="OBSERVE", expected_mode="ACTIVE", command_id="panel-observe")
        self.assertTrue(observed["changed"])
        bypass = self.participation.set_mode(mode="BYPASS", expected_mode="OBSERVE", command_id="panel-bypass")
        self.assertEqual(bypass["mode"], "BYPASS")
        self.assertEqual(RuntimeModeService(self.store, AuthorityService(self.store, self.store.policy)).current()["mode"], "NORMAL")
        with self.store._connection() as conn:
            after = tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                          for table in ("objects", "tasks", "runs", "memory_candidates"))
        self.assertEqual(after, before)
        self.store.close()
        self.store = ObjectStore(self.root)
        self.addCleanup(self.store.close)
        self.participation = ParticipationModeService(self.store)
        self.assertEqual(self.participation.current()["mode"], "BYPASS")
        self.participation.set_mode(mode="ACTIVE", expected_mode="BYPASS", command_id="panel-reactivate")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM participation_mode_events").fetchone()[0], 3)

    def test_active_can_transition_directly_to_bypass(self):
        result = self.participation.set_mode(
            mode="BYPASS", expected_mode="ACTIVE", command_id="panel-direct-bypass"
        )
        self.assertEqual(result["previous_mode"], "ACTIVE")
        self.assertEqual(result["mode"], "BYPASS")
        self.assertEqual(self.participation.current()["mode"], "BYPASS")

    def test_disengagement_refuses_in_flight_task_without_mutating_it(self):
        policy = json.loads((Path(__file__).resolve().parents[2] / "policies" / "default-policy.json").read_text(encoding="utf-8"))
        authority = AuthorityService(self.store, policy)
        authority.register_principal({"schema_id": "nexus.principal", "schema_version": 1,
                                      "principal_id": "panel-human", "principal_type": "HUMAN", "status": "ACTIVE"}, "panel-human")
        trace = TraceRuntime(self.store, authority)
        trace.create_task({"schema_id": "nexus.task", "schema_version": 1, "task_id": "panel-open-task",
                           "requester_id": "panel-human", "status": "CREATED",
                           "created_at": datetime.now(timezone.utc).isoformat(), "command_id": "panel-task-create"})
        with self.assertRaisesRegex(RuntimeDenied, "PARTICIPATION_CHANGE_BLOCKED_ACTIVE_TASKS"):
            self.participation.set_mode(mode="BYPASS", expected_mode="ACTIVE", command_id="panel-unsafe-bypass")
        self.assertEqual(self.participation.current()["mode"], "ACTIVE")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM tasks WHERE task_id='panel-open-task'").fetchone()[0], "CREATED")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM participation_mode_events").fetchone()[0], 0)

    def _insert_unfinished_effect(self, *, approval_bound: bool) -> None:
        now = "2026-09-27T00:00:00Z"
        effect_id = "panel-pending-effect-approval" if approval_bound else "panel-pending-effect"
        effect = {
            "effect_id": effect_id, "run_id": "panel-effect-run-approval" if approval_bound else "panel-effect-run",
            "idempotency_key": effect_id + "-key", "execution_state": "DECLARED",
            "effect_outcome": "UNDETERMINED", "reconciliation_status": "NOT_REQUIRED",
        }
        self._external_execute(
            "INSERT INTO effects(effect_id,run_id,tool_id,tool_descriptor_version,action_type,target_ref,"
            "payload_integrity_hash,payload_object_ref,idempotency_key,grant_id,approval_ref,budget_reservation_ref,"
            "execution_state,effect_outcome,reconciliation_status,external_receipt_ref,effect_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (effect_id, effect["run_id"], "tool", "1", "READ", "resource", "0" * 64,
             "panel-payload", effect["idempotency_key"], "panel-grant", "panel-approval" if approval_bound else None,
             "panel-reservation", "DECLARED", "UNDETERMINED", "NOT_REQUIRED", None,
             json.dumps(effect, sort_keys=True), now, now),
        )

    def test_disengagement_refuses_in_flight_run(self):
        self._external_execute(
            "INSERT INTO runs(run_id,task_id,executor_kind,status,grant_id,data_boundary_json,"
            "classification_assertion_ref,created_at) VALUES(?,?,?,?,?,?,?,?)",
            ("panel-active-run", "panel-task", "ORCHESTRATOR", "RUNNING", "panel-grant",
             '{"allowed_classifications":["PUBLIC"],"handling_tags":[]}', "panel-class", "2026-09-27T00:00:00Z"),
        )
        with self.assertRaisesRegex(RuntimeDenied, "PARTICIPATION_CHANGE_BLOCKED_ACTIVE_RUNS"):
            self.participation.set_mode(mode="OBSERVE", expected_mode="ACTIVE", command_id="panel-active-run-block")
        with self.store._connection() as conn:
            conn.execute("UPDATE runs SET status='CANCELLED' WHERE run_id='panel-active-run'")

    def test_disengagement_refuses_pending_effect_and_approval_bound_commit(self):
        self._insert_unfinished_effect(approval_bound=False)
        with self.assertRaisesRegex(RuntimeDenied, "PARTICIPATION_CHANGE_BLOCKED_PENDING_EFFECTS"):
            self.participation.set_mode(mode="OBSERVE", expected_mode="ACTIVE", command_id="panel-pending-effect-block")
        completed = {
            "effect_id": "panel-pending-effect", "run_id": "panel-effect-run",
            "idempotency_key": "panel-pending-effect-key", "execution_state": "CANCELLED",
            "effect_outcome": "NOT_COMMITTED", "reconciliation_status": "NOT_REQUIRED",
        }
        with self.store._connection() as conn:
            conn.execute("UPDATE effects SET execution_state='CANCELLED',effect_outcome='NOT_COMMITTED',effect_json=? "
                         "WHERE effect_id='panel-pending-effect'", (json.dumps(completed, sort_keys=True),))
        self._insert_unfinished_effect(approval_bound=True)
        with self.assertRaisesRegex(RuntimeDenied, "PARTICIPATION_CHANGE_BLOCKED_APPROVAL_OR_COMMIT"):
            self.participation.set_mode(mode="BYPASS", expected_mode="ACTIVE", command_id="panel-approval-block")
        self.assertEqual(self.participation.current()["mode"], "ACTIVE")

    def test_observe_and_bypass_block_automatic_host_bridge_ingestion(self):
        bridge = CodexHostedBridge(store=self.store, authority=None, budget=None, trace=None, runtime=None, verifier=None)
        for previous, mode in (("ACTIVE", "OBSERVE"), ("OBSERVE", "BYPASS")):
            self.participation.set_mode(mode=mode, expected_mode=previous, command_id="bridge-mode-" + mode)
            with self.assertRaisesRegex(RuntimeDenied, "PARTICIPATION_MODE_DISALLOWS_AUTOMATIC_INGESTION"):
                bridge.create_task_root(
                    command_id="must-not-run", task_id="t", requester_id="r", grant_id="g", root_run_id="rr",
                    budget_account_id="b", budget_limits={}, input_object_id="i", input_payload=b"",
                    task_contract={}, contract_object_id="c", dag_nodes=[], root_manifest_object_id="m",
                    data_boundary={}, classifications={},
                )

    def test_panel_startup_viewmodel_unknowns_and_tabs_are_truthful(self):
        self.store.close()
        application = open_panel_application(self.root)
        try:
            snapshot = application.view_model.snapshot()
            self.assertEqual(snapshot["participation_mode"], "ACTIVE")
            self.assertEqual(snapshot["runtime_mode"], "NORMAL")
            self.assertEqual(snapshot["context_status"]["status"], "NOT COMPILED")
            self.assertEqual(snapshot["skill_status"]["status"], "NOT IMPLEMENTED")
            for name in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens",
                         "latency", "context_pack_size", "skill_instructions_selected",
                         "skill_instructions_loaded", "duplicate_work_reused_count",
                         "duplicate_work_avoided_count", "estimated_cost_or_savings"):
                metric = snapshot["value_metrics"][name]
                self.assertIsNone(metric["value"])
                self.assertEqual(metric["provenance"], "UNAVAILABLE")
            self.assertEqual(set(snapshot["metric_provenance_values"]),
                             {"OBSERVED", "HOST_DECLARED", "DERIVED", "ESTIMATED", "UNAVAILABLE"})
            self.assertFalse(snapshot["memory"]["payloads_included"])
            serialized = json.dumps(snapshot, sort_keys=True)
            self.assertNotIn(str(self.root), serialized)
            from adapters.panel.ui import _TABS
            self.assertEqual(_TABS, ("OVERVIEW", "TASKS", "MEMORY", "CONTEXT", "SKILLS", "VALUE"))
            ui_source = (Path(__file__).resolve().parents[2] / "adapters" / "panel" / "ui.py").read_text(encoding="utf-8")
            self.assertNotIn("sqlite3", ui_source)
            self.assertNotIn("ObjectStore", ui_source)
        finally:
            application.close()

    def test_recovery_mode_preserves_unavailable_instead_of_reporting_zero(self):
        self.store.close()
        application = open_panel_application(self.root)
        try:
            with application.store._connection() as conn:
                conn.execute("UPDATE runtime_mode_state SET mode='RECOVERY' WHERE singleton=1")
            snapshot = application.view_model.snapshot()
            self.assertEqual(snapshot["runtime_mode"], "RECOVERY")
            self.assertEqual(snapshot["query_status"], "RUNTIME_RECOVERY_CORE_BYPASS")
            self.assertEqual(snapshot["context_status"]["status"], "UNAVAILABLE")
            for key in ("task_count", "recorded_run_count", "unfinished_task_count", "blocked_task_count",
                        "active_run_count", "pending_effect_count"):
                self.assertIsNone(snapshot["overview"][key])
            self.assertEqual(snapshot["value_metrics"]["core_recorded_run_count"]["value"], None)
            self.assertEqual(snapshot["value_metrics"]["core_recorded_run_count"]["provenance"], "UNAVAILABLE")
            self.assertEqual(snapshot["memory"]["status"], "UNAVAILABLE")
            self.assertIsNone(snapshot["memory"]["raw_history_count"])
            self.assertIsNone(snapshot["memory"]["admitted_count"])
        finally:
            application.close()


if __name__ == "__main__":
    unittest.main()
