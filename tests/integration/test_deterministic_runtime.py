import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from adapters.storage import ObjectStore
from kernel.authority import AuthorityService
from kernel.budget import BudgetService
from kernel.run import TraceAdmissionDenied, TraceRuntime
from kernel.runtime import DeterministicRuntime
from kernel.runtime.errors import RuntimeDenied


class DeterministicRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nexus-step5-")
        self.addCleanup(self.temp.cleanup)
        self.store = ObjectStore(Path(self.temp.name) / "data")
        self.addCleanup(self.store.close)
        self.policy = json.loads((Path(__file__).resolve().parents[2] / "policies" / "default-policy.json").read_text(encoding="utf-8"))
        self.policy["trust_anchors"] = ["human-root"]
        self.authority = AuthorityService(self.store, self.policy)
        self.budget = BudgetService(self.store)
        self.trace = TraceRuntime(self.store, self.authority)
        self.runtime = DeterministicRuntime(self.store, self.authority, self.budget, self.trace)
        for principal_id, principal_type in (("human-root", "HUMAN"), ("agent", "SERVICE"), ("model-agent", "MODEL"), ("tool-agent", "TOOL")):
            self.authority.register_principal({"schema_id": "nexus.principal", "schema_version": 1, "principal_id": principal_id, "principal_type": principal_type, "status": "ACTIVE"}, "cmd-principal-" + principal_id)
        self.authority.register_trust_anchor({"schema_id": "nexus.trust_anchor", "schema_version": 1, "anchor_id": "anchor-root", "principal_id": "human-root", "policy_ref": "1"}, "cmd-anchor")
        now = datetime.now(timezone.utc)
        self.root_resources = ["run-root", "run-e0", "run-e1", "run-e2", "run-tool", "model-e0", "model-e1", "model-e2", "model-cloud", "tool-read"]
        self.authority.create_grant({"schema_id": "nexus.delegation_grant", "schema_version": 1, "grant_id": "grant-root", "issued_by": "human-root", "granted_to": "agent", "task_scope": ["task-1"], "resource_scope": self.root_resources, "action_scope": ["RUN_CREATE", "RUN_TRANSITION", "TRACE_APPEND", "DELEGATE", "OBJECT_WRITE", "CLASSIFY", "RUNTIME_CONFIGURE", "TOOL_READ"], "audience_scope": ["nexus-runtime"], "issued_at": now.isoformat(), "expires_at": (now + timedelta(days=300)).isoformat(), "status": "ACTIVE", "policy_version": "1"}, "cmd-root-grant")
        self.trace.create_task({"schema_id": "nexus.task", "schema_version": 1, "task_id": "task-1", "requester_id": "human-root", "status": "CREATED", "created_at": self._now(), "command_id": "cmd-task"})
        self.budget.create_account(command_id="cmd-budget", account_id="budget-1", task_id="task-1", amount_limit=100, unit="credits", model_call_limit=10, tool_call_limit=10, child_run_limit=10)
        self._classify("class-root-run", "RUN", "run-root", "agent")
        self._classify("class-root-created", "TRACE_EVENT", "evt-cmd-root-create", "agent")
        self.trace.create_run({"schema_id": "nexus.run", "schema_version": 1, "run_id": "run-root", "task_id": "task-1", "executor_kind": "ORCHESTRATOR", "status": "CREATED", "grant_id": "grant-root", "data_boundary": self._boundary(), "classification_assertion_ref": "class-root-run", "created_at": self._now()}, command_id="cmd-root-create", event_classification_assertion_ref="class-root-created")
        self._classify("class-input", "OBJECT", "input-1", "agent")
        self.store.put_object(command_id="cmd-input", object_id="input-1", payload=b"synthetic text input", object_type="user_input", created_by_run="run-root", classification_assertion_ref="class-input")
        contract = {"schema_id": "nexus.task_contract", "schema_version": 1, "task_id": "task-1", "requester_id": "human-root", "goal": "synthetic runtime scheduling test", "constraints": [], "input_object_refs": ["input-1"], "success_criteria": ["typed fake plan is recorded"], "risk_class": "STANDARD", "budget_account_ref": "budget-1", "routing_constraints": {"allowed_providers": ["fake"], "forbidden_providers": [], "locality": "LOCAL_ONLY", "network_required": False, "modalities": ["text"]}, "routing_preferences": {"optimize_for": "COST"}, "created_at": self._now()}
        self._classify("class-contract", "OBJECT", "contract-1", "agent")
        self.runtime.bind_task_contract(command_id="cmd-contract", root_run_id="run-root", contract_object_id="contract-1", classification_assertion_ref="class-contract", contract=contract)

    def _activate_root(self):
        root_manifest = {"schema_id": "nexus.run_manifest", "schema_version": 1, "executor_kind": "ORCHESTRATOR", "runtime_version": "0.1", "policy_version": "1", "schema_versions": {"nexus.run_manifest": 1}, "input_object_refs": ["input-1"], "authority_grant_ref": "grant-root", "data_boundary": self._boundary(), "classification_assertion_ref": "class-root-run", "task_contract_ref": "contract-1", "dag_version": "1", "scheduler_version": "1"}
        self._classify("class-root-manifest", "OBJECT", "manifest-root", "agent")
        self.runtime.bind_manifest(command_id="cmd-root-manifest", run_id="run-root", manifest_object_id="manifest-root", manifest_classification_assertion_ref="class-root-manifest", manifest=root_manifest)
        self._classify("class-root-ready", "TRACE_EVENT", "evt-cmd-root-ready", "agent")
        self.trace.transition_run(command_id="cmd-root-ready", run_id="run-root", expected_state="CREATED", next_state="READY", classification_assertion_ref="class-root-ready")
        self._classify("class-root-running", "TRACE_EVENT", "evt-cmd-root-running", "agent")
        self.trace.transition_run(command_id="cmd-root-running", run_id="run-root", expected_state="READY", next_state="RUNNING", classification_assertion_ref="class-root-running")

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _boundary():
        return {"allowed_classifications": ["PUBLIC", "PERSONAL"], "handling_tags": []}

    def _classify(self, assertion_id, subject_type, subject_ref, actor_id):
        with self.store._connection() as conn:
            if conn.execute("SELECT 1 FROM classification_assertions WHERE assertion_id=?", (assertion_id,)).fetchone():
                return
            conn.execute("INSERT INTO classification_assertions(assertion_id,subject_type,subject_ref,sensitivity_level,handling_tags_json,policy_version,reason,actor_id) VALUES(?,?,?,?,?,?,?,?)", (assertion_id, subject_type, subject_ref, "PUBLIC", "[]", "1", "synthetic Step 5 test", actor_id))

    def _event_class(self, run_command_id, actor_id):
        event_id = "evt-" + run_command_id
        assertion_id = "class-" + event_id
        self._classify(assertion_id, "TRACE_EVENT", event_id, actor_id)
        return assertion_id

    def _child_grant(self, run_id, principal_id, grant_id):
        with self.store._connection() as conn:
            if conn.execute("SELECT 1 FROM delegation_grants WHERE grant_id=?", (grant_id,)).fetchone():
                return
        now = datetime.now(timezone.utc)
        self.authority.create_grant({"schema_id": "nexus.delegation_grant", "schema_version": 1, "grant_id": grant_id, "parent_grant_id": "grant-root", "issued_by": "agent", "granted_to": principal_id, "task_scope": ["task-1"], "resource_scope": [run_id], "action_scope": ["RUN_CREATE", "RUN_TRANSITION", "TRACE_APPEND", "OBJECT_WRITE", "TOOL_READ"], "audience_scope": ["nexus-runtime"], "issued_at": now.isoformat(), "expires_at": (now + timedelta(days=30)).isoformat(), "status": "ACTIVE", "policy_version": "1"}, "cmd-" + grant_id)

    def _model_profile(self, model_id, model_class, cost, *, provider="fake", local="LOCAL"):
        profile = {"schema_id": "nexus.model_profile", "schema_version": 1, "model_id": model_id, "provider": provider, "model_class": model_class, "modalities": ["text"], "context_limit": 4096, "structured_output_support": True, "tool_use_support": False, "local_or_cloud": local, "allowed_classifications": ["PUBLIC", "PERSONAL"], "provider_policy_ref": "fake-policy-1", "cost_profile": {"unit": "credits", "estimated_cost": cost}, "latency_profile": {"estimated_ms": 100 * cost}, "local_eval_status": "PASSED", "version": "1", "model_adapter_version": "fake-adapter-1", "available": True}
        self.runtime.register_model_profile(command_id="cmd-register-" + model_id, grant_id="grant-root", task_id="task-1", profile=profile)

    def _nodes(self):
        result = []
        for node_id, quality, run_class in (("node-e0", "ROUTINE", "LOW"), ("node-e1", "STANDARD", "STANDARD"), ("node-e2", "CRITICAL", "CRITICAL")):
            result.append({"schema_id": "nexus.subtask", "schema_version": 1, "subtask_id": node_id, "task_id": "task-1", "input_object_refs": ["input-1"], "input_schema_id": "nexus.object@1.schema.json", "output_schema_id": "nexus.object@1.schema.json", "dependency_ids": [], "quality_requirement": quality, "risk_class": run_class, "validation_method": "SCHEMA", "budget_amount": 10, "requested_executor": "MODEL", "required_modalities": ["text"], "requires_structured_output": True, "requires_tool_use": False, "created_at": "2026-09-24T00:00:00Z"})
        result.append({"schema_id": "nexus.subtask", "schema_version": 1, "subtask_id": "node-tool", "task_id": "task-1", "input_object_refs": ["input-1"], "input_schema_id": "nexus.object@1.schema.json", "output_schema_id": "nexus.object@1.schema.json", "dependency_ids": [], "quality_requirement": "ROUTINE", "risk_class": "LOW", "validation_method": "TEST", "budget_amount": 2, "requested_executor": "TOOL", "tool_id": "tool-read", "required_modalities": ["text"], "created_at": "2026-09-24T00:00:00Z"})
        return result

    def _schedule(self, node_id, run_id, actor_id, grant_id, route_id=None):
        self._child_grant(run_id, actor_id, grant_id)
        self._classify("class-" + run_id, "RUN", run_id, actor_id)
        create_command = "cmd-schedule-" + node_id + "-create-run"
        create_event = self._event_class(create_command, actor_id)
        ready_event = self._event_class("cmd-schedule-" + node_id + "-ready", actor_id)
        if route_id:
            self._classify("class-" + route_id, "OBJECT", route_id, "agent")
        manifest_id = "manifest-" + run_id
        self._classify("class-" + manifest_id, "OBJECT", manifest_id, actor_id)
        return self.runtime.schedule_node(command_id="cmd-schedule-" + node_id, task_id="task-1", root_run_id="run-root", subtask_id=node_id, child_run_id=run_id, child_grant_id=grant_id, child_classification_assertion_ref="class-" + run_id, event_classification_assertion_ref=create_event, ready_event_classification_assertion_ref=ready_event, route_object_id=route_id, route_classification_assertion_ref="class-" + route_id if route_id else None, manifest_object_id=manifest_id, manifest_classification_assertion_ref="class-" + manifest_id)

    def _advance(self, run_id, command_id, expected, target, actor_id):
        classification = self._event_class(command_id, actor_id)
        return self.trace.transition_run(command_id=command_id, run_id=run_id, expected_state=expected, next_state=target, classification_assertion_ref=classification)

    def test_cycle_is_rejected_and_dag_route_budget_and_child_manifests_pass(self):
        nodes = self._nodes()
        nodes[0]["dependency_ids"] = ["node-e1"]
        nodes[1]["dependency_ids"] = ["node-e0"]
        with self.assertRaisesRegex(RuntimeDenied, "DAG_CYCLE"):
            self.runtime.create_dag(command_id="cmd-cycle", task_id="task-1", root_run_id="run-root", nodes=nodes)
        nodes = self._nodes()
        nodes[1]["dependency_ids"] = ["node-e0"]
        self.runtime.create_dag(command_id="cmd-dag", task_id="task-1", root_run_id="run-root", nodes=nodes)
        self._activate_root()
        for args in (("model-e0", "E0", 1), ("model-e1", "E1", 2), ("model-e2", "E2", 3), ("model-cloud", "E2", 1)):
            self._model_profile(*args, local="CLOUD" if args[0] == "model-cloud" else "LOCAL")
        descriptor = {"schema_id": "nexus.tool_descriptor", "schema_version": 1, "tool_id": "tool-read", "version": "1", "input_schema_id": "nexus.object@1.schema.json", "output_schema_id": "nexus.object@1.schema.json", "effect_class": "READ_ONLY", "required_authority": ["TOOL_READ"], "required_classifications": ["PUBLIC"], "idempotency_support": True, "reconciliation_capability": "NOT_APPLICABLE_READ_ONLY", "compensation_capability": "NOT_APPLICABLE_READ_ONLY", "network_egress": False, "risk_tags": [], "review_status": "APPROVED"}
        unreviewed_write_descriptor = {**descriptor, "tool_id": "tool-write", "effect_class": "EXTERNAL_REVERSIBLE"}
        with self.assertRaisesRegex(RuntimeDenied, "STEP5_ONLY_APPROVED_READ_ONLY_TOOLS"):
            self.runtime.register_tool_descriptor(command_id="cmd-register-write-tool", grant_id="grant-root", task_id="task-1", descriptor=unreviewed_write_descriptor)
        with self.store._connection() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM tool_descriptors WHERE tool_id='tool-write'").fetchone())
        self.runtime.register_tool_descriptor(command_id="cmd-register-tool", grant_id="grant-root", task_id="task-1", descriptor=descriptor)
        results = [self._schedule("node-e0", "run-e0", "model-agent", "grant-e0", "route-e0")]
        self.assertEqual(self.runtime.replay_subtask("node-e0")["status"], "READY")
        with self.assertRaisesRegex(RuntimeDenied, "SUBTASK_DEPENDENCY_NOT_SATISFIED"):
            self._schedule("node-e1", "run-e1", "model-agent", "grant-e1", "route-e1")
        self._advance("run-e0", "cmd-node-e0-running", "READY", "RUNNING", "model-agent")
        self._advance("run-e0", "cmd-node-e0-verifying", "RUNNING", "VERIFYING", "model-agent")
        self._advance("run-e0", "cmd-node-e0-succeeded", "VERIFYING", "SUCCEEDED", "model-agent")
        self.assertEqual(self.runtime.replay_subtask("node-e0")["status"], "SUCCEEDED")
        results.append(self._schedule("node-e1", "run-e1", "model-agent", "grant-e1", "route-e1"))
        results.append(self._schedule("node-e2", "run-e2", "model-agent", "grant-e2", "route-e2"))
        results.append(self._schedule("node-tool", "run-tool", "tool-agent", "grant-tool"))
        self.assertEqual([r["status"] for r in results], ["READY"] * 4)
        self.assertEqual([self.trace.replay_run(r["run_id"])["status"] for r in results], ["SUCCEEDED", "READY", "READY", "READY"])
        with self.store._connection() as conn:
            routed = {row["subtask_id"]: json.loads(row["decision_json"]) for row in conn.execute("SELECT subtask_id,decision_json FROM route_decisions")}
            budget = conn.execute("SELECT reserved,model_calls_reserved,tool_calls_reserved,child_runs_reserved FROM budget_accounts WHERE account_id='budget-1'").fetchone()
        self.assertEqual(routed["node-e0"]["selected_model_class"], "E0")
        self.assertEqual(routed["node-e1"]["selected_model_class"], "E1")
        self.assertEqual(routed["node-e2"]["selected_model_class"], "E2")
        self.assertIn({"model_id": "model-cloud", "reason_code": "LOCALITY_CONSTRAINT"}, routed["node-e1"]["exclusion_reasons"])
        self.assertEqual(tuple(budget), (8, 3, 1, 4))
        self.assertEqual(self._schedule("node-e0", "run-e0", "model-agent", "grant-e0", "route-e0"), results[0])

    def test_dag_rejects_unknown_output_schema_and_route_never_downgrades_quality(self):
        nodes = self._nodes()
        nodes[0]["output_schema_id"] = "..\\outside.schema.json"
        with self.assertRaises(RuntimeDenied):
            self.runtime.create_dag(command_id="cmd-bad-schema", task_id="task-1", root_run_id="run-root", nodes=nodes)
        self.runtime.create_dag(command_id="cmd-dag", task_id="task-1", root_run_id="run-root", nodes=self._nodes())
        self._activate_root()
        self._model_profile("model-e1", "E1", 1)
        node = self._nodes()[2]
        with self.assertRaisesRegex(RuntimeDenied, "NO_MODEL_MEETS_HARD_ROUTING_AND_QUALITY_CONSTRAINTS"):
            self.runtime._choose_model(node, self.runtime._contract("task-1")[1], self._root(), self.runtime._budget_snapshot("budget-1"), self.runtime._contract("task-1")[0])

    def _root(self):
        with self.store._connection() as conn:
            return conn.execute("SELECT * FROM runs WHERE run_id='run-root'").fetchone()


if __name__ == "__main__":
    unittest.main()
