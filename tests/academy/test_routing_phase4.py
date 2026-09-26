"""Phase 4 probes over existing deterministic, synthetic routing semantics."""
from __future__ import annotations

import json
import unittest

from kernel.authority.errors import InvalidDelegation
from kernel.object.errors import CommandConflict
from kernel.runtime.errors import RuntimeDenied
from tests.integration.test_deterministic_runtime import DeterministicRuntimeTests


class RoutingPhase4Tests(unittest.TestCase):
    def _fixture(self):
        fixture = DeterministicRuntimeTests("test_cycle_is_rejected_and_dag_route_budget_and_child_manifests_pass")
        fixture.setUp()
        self.addCleanup(fixture.temp.cleanup)
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_exact_route_replay_conflict_and_no_host_receipt(self):
        fixture = self._fixture()
        fixture.runtime.create_dag(command_id="p4-dag", task_id="task-1", root_run_id="run-root", nodes=fixture._nodes())
        fixture._activate_root()
        fixture._model_profile("model-e0", "E0", 1)
        fixture._model_profile("model-e1", "E1", 2)
        args = {"node_id":"node-e0", "run_id":"run-e0", "actor_id":"model-agent", "grant_id":"p4-grant-1",
                "route_id":"route-e0", "command_id":"p4-schedule-1", "requested_capability":"E0"}
        first = fixture._schedule(**args)
        with fixture.store._connection() as conn:
            before = tuple(conn.execute("SELECT (SELECT COUNT(*) FROM route_decisions),(SELECT COUNT(*) FROM runs),(SELECT COUNT(*) FROM budget_reservations),(SELECT COUNT(*) FROM trace_events),(SELECT COUNT(*) FROM command_ledger)").fetchone())
            route = json.loads(conn.execute("SELECT decision_json FROM route_decisions WHERE decision_object_id='route-e0'").fetchone()[0])
            manifest_id = conn.execute("SELECT manifest_ref FROM runs WHERE run_id='run-e0'").fetchone()[0]
        exact = fixture._schedule(**args)
        self.assertEqual(exact, first)
        with self.assertRaises(CommandConflict):
            fixture._schedule(**{**args, "requested_capability":"E1"})
        with fixture.store._connection() as conn:
            after = tuple(conn.execute("SELECT (SELECT COUNT(*) FROM route_decisions),(SELECT COUNT(*) FROM runs),(SELECT COUNT(*) FROM budget_reservations),(SELECT COUNT(*) FROM trace_events),(SELECT COUNT(*) FROM command_ledger)").fetchone())
        self.assertEqual(after, before)
        self.assertEqual(route["selected_model_class"], "E0")
        self.assertNotEqual(route.get("execution_source"), "CODEX_HOST_DECLARED")
        self.assertNotIn("provider_identity", route)
        manifest = json.loads(fixture.store.get_payload(manifest_id).decode("utf-8"))
        self.assertNotEqual(manifest.get("execution_source"), "CODEX_HOST_DECLARED")
        self.assertEqual(manifest.get("model_identity_status"), None)

    def test_hard_budget_insufficient_rejects_route_before_reservation(self):
        fixture = self._fixture()
        fixture.runtime.create_dag(command_id="p4-budget-dag", task_id="task-1", root_run_id="run-root", nodes=fixture._nodes())
        fixture._activate_root()
        fixture._model_profile("model-e1", "E1", 1)
        fixture._model_profile("model-e2", "E2", 101)
        first = fixture._schedule("node-e1", "run-e1", "model-agent", "p4-budget-grant-e1", "route-e1",
            command_id="p4-budget-schedule-e1", requested_capability="E1")
        fixture._advance("run-e1", "p4-budget-running-e1", "READY", "RUNNING", "model-agent")
        fixture._advance("run-e1", "p4-budget-failed-e1", "RUNNING", "FAILED", "model-agent")
        with fixture.store._connection() as conn:
            before = tuple(conn.execute("SELECT (SELECT COUNT(*) FROM runs WHERE subtask_id='node-e1'),(SELECT COUNT(*) FROM budget_reservations WHERE run_id IN ('run-e1','run-e2')),(SELECT COUNT(*) FROM subtask_attempts WHERE subtask_id='node-e1'),(SELECT COUNT(*) FROM route_decisions WHERE subtask_id='node-e1')").fetchone())
        with self.assertRaisesRegex(RuntimeDenied, "NO_MODEL_MEETS_HARD_ROUTING_AND_QUALITY_CONSTRAINTS"):
            fixture._schedule("node-e1", "run-e2", "model-agent", "p4-budget-grant-e2", "route-e2",
                command_id="p4-budget-schedule-e2", requested_capability="E2",
                attempt_reason="VERIFIER_REQUIRED_ESCALATION", predecessor_attempt_id=first["attempt_id"])
        with fixture.store._connection() as conn:
            after = tuple(conn.execute("SELECT (SELECT COUNT(*) FROM runs WHERE subtask_id='node-e1'),(SELECT COUNT(*) FROM budget_reservations WHERE run_id IN ('run-e1','run-e2')),(SELECT COUNT(*) FROM subtask_attempts WHERE subtask_id='node-e1'),(SELECT COUNT(*) FROM route_decisions WHERE subtask_id='node-e1')").fetchone())
        self.assertEqual(after, before)

    def test_revoked_parent_authority_denies_fresh_escalation(self):
        fixture = self._fixture()
        fixture.runtime.create_dag(command_id="p4-revoke-dag", task_id="task-1", root_run_id="run-root", nodes=fixture._nodes())
        fixture._activate_root()
        fixture._model_profile("model-e1", "E1", 1)
        fixture._model_profile("model-e2", "E2", 2)
        first = fixture._schedule("node-e1", "run-e1", "model-agent", "p4-grant-e1", "route-e1",
            command_id="p4-schedule-e1", requested_capability="E1")
        fixture._advance("run-e1", "p4-running-e1", "READY", "RUNNING", "model-agent")
        fixture._advance("run-e1", "p4-failed-e1", "RUNNING", "FAILED", "model-agent")
        fixture.authority.revoke_grant("grant-root", "p4-revoke-root")
        with fixture.store._connection() as conn:
            before = tuple(conn.execute("SELECT (SELECT COUNT(*) FROM runs WHERE parent_run_id='run-root'),(SELECT COUNT(*) FROM budget_reservations),(SELECT COUNT(*) FROM subtask_attempts WHERE subtask_id='node-e1'),(SELECT COUNT(*) FROM route_decisions WHERE subtask_id='node-e1')").fetchone())
        with self.assertRaises(InvalidDelegation):
            fixture._schedule("node-e1", "run-e2", "model-agent", "p4-grant-e2", "route-e2",
                command_id="p4-schedule-e2", requested_capability="E2",
                attempt_reason="VERIFIER_REQUIRED_ESCALATION", predecessor_attempt_id=first["attempt_id"])
        with fixture.store._connection() as conn:
            after = tuple(conn.execute("SELECT (SELECT COUNT(*) FROM runs WHERE parent_run_id='run-root'),(SELECT COUNT(*) FROM budget_reservations),(SELECT COUNT(*) FROM subtask_attempts WHERE subtask_id='node-e1'),(SELECT COUNT(*) FROM route_decisions WHERE subtask_id='node-e1')").fetchone())
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
