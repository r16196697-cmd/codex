import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from adapters.storage import ObjectStore
from adapters.client.operator import OperatorClient
from kernel.authority import AuthorityService
from kernel.authority.errors import ApprovalDenied, AuthorizationDenied, InvalidDelegation
from kernel.budget import BudgetService
from kernel.run import TraceAdmissionDenied, TraceRuntime
from kernel.effect import AmbiguousDispatch, DeterministicEffectService
from kernel.runtime import DeterministicRuntime
from kernel.runtime.inspect import InspectService
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
        self.root_resources = ["runtime-mode:instance", "run-root", "run-e0", "run-e1", "run-e2", "run-tool", "run-tool-comp", "run-fail-e1", "run-fail-e2", "run-e2-downstream", "run-illegal-third", "model-e0", "model-e1", "model-e2", "model-cloud", "tool-read", "sandbox-target"]
        self.authority.create_grant({"schema_id": "nexus.delegation_grant", "schema_version": 1, "grant_id": "grant-root", "issued_by": "human-root", "granted_to": "agent", "task_scope": ["task-1"], "resource_scope": self.root_resources, "action_scope": ["RUN_CREATE", "RUN_TRANSITION", "TRACE_APPEND", "DELEGATE", "OBJECT_WRITE", "CLASSIFY", "RUNTIME_CONFIGURE", "TOOL_READ", "EFFECT_PREPARE", "EFFECT_COMMIT", "EFFECT_RECONCILE", "EFFECT_COMPENSATE", "FAKE_WRITE"], "audience_scope": ["nexus-runtime"], "issued_at": now.isoformat(), "expires_at": (now + timedelta(days=300)).isoformat(), "status": "ACTIVE", "policy_version": "1"}, "cmd-root-grant")
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
        self.authority.create_grant({"schema_id": "nexus.delegation_grant", "schema_version": 1, "grant_id": grant_id, "parent_grant_id": "grant-root", "issued_by": "agent", "granted_to": principal_id, "task_scope": ["task-1"], "resource_scope": [run_id, "sandbox-target", "tool-read"], "action_scope": ["RUN_CREATE", "RUN_TRANSITION", "TRACE_APPEND", "OBJECT_WRITE", "TOOL_READ", "EFFECT_PREPARE", "EFFECT_COMMIT", "EFFECT_RECONCILE", "EFFECT_COMPENSATE", "FAKE_WRITE"], "audience_scope": ["nexus-runtime"], "issued_at": now.isoformat(), "expires_at": (now + timedelta(days=30)).isoformat(), "status": "ACTIVE", "policy_version": "1"}, "cmd-" + grant_id)

    def _model_profile(self, model_id, model_class, cost, *, provider="fake", local="LOCAL"):
        profile = {"schema_id": "nexus.model_profile", "schema_version": 1, "model_id": model_id, "provider": provider, "model_class": model_class, "modalities": ["text"], "context_limit": 4096, "structured_output_support": True, "tool_use_support": False, "local_or_cloud": local, "allowed_classifications": ["PUBLIC", "PERSONAL"], "provider_policy_ref": "fake-policy-1", "cost_profile": {"unit": "credits", "estimated_cost": cost}, "latency_profile": {"estimated_ms": 100 * cost}, "local_eval_status": "PASSED", "version": "1", "model_adapter_version": "fake-adapter-1", "available": True}
        self.runtime.register_model_profile(command_id="cmd-register-" + model_id, grant_id="grant-root", task_id="task-1", profile=profile)

    def _nodes(self):
        result = []
        for node_id, quality, run_class in (("node-e0", "ROUTINE", "LOW"), ("node-e1", "STANDARD", "STANDARD"), ("node-e2", "CRITICAL", "CRITICAL")):
            result.append({"schema_id": "nexus.subtask", "schema_version": 1, "subtask_id": node_id, "task_id": "task-1", "input_object_refs": ["input-1"], "input_schema_id": "nexus.object@1.schema.json", "output_schema_id": "nexus.object@1.schema.json", "dependency_ids": [], "quality_requirement": quality, "risk_class": run_class, "validation_method": "SCHEMA", "budget_amount": 10, "requested_executor": "MODEL", "required_modalities": ["text"], "requires_structured_output": True, "requires_tool_use": False, "created_at": "2026-09-24T00:00:00Z"})
        result.append({"schema_id": "nexus.subtask", "schema_version": 1, "subtask_id": "node-tool", "task_id": "task-1", "input_object_refs": ["input-1"], "input_schema_id": "nexus.object@1.schema.json", "output_schema_id": "nexus.object@1.schema.json", "dependency_ids": [], "quality_requirement": "ROUTINE", "risk_class": "LOW", "validation_method": "TEST", "budget_amount": 2, "requested_executor": "TOOL", "tool_id": "tool-read", "required_modalities": ["text"], "created_at": "2026-09-24T00:00:00Z"})
        result.append({"schema_id": "nexus.subtask", "schema_version": 1, "subtask_id": "node-tool-comp", "task_id": "task-1", "input_object_refs": ["input-1"], "input_schema_id": "nexus.object@1.schema.json", "output_schema_id": "nexus.object@1.schema.json", "dependency_ids": [], "quality_requirement": "ROUTINE", "risk_class": "LOW", "validation_method": "TEST", "budget_amount": 2, "requested_executor": "TOOL", "tool_id": "tool-read", "required_modalities": ["text"], "created_at": "2026-09-24T00:00:00Z"})
        return result

    def _schedule(self, node_id, run_id, actor_id, grant_id, route_id=None, *, command_id=None, requested_capability=None, attempt_reason="INITIAL", predecessor_attempt_id=None):
        route_id = route_id or "route-" + run_id
        self._child_grant(run_id, actor_id, grant_id)
        self._classify("class-" + run_id, "RUN", run_id, actor_id)
        command_id = command_id or "cmd-schedule-" + node_id
        create_command = command_id + "-create-run"
        create_event = self._event_class(create_command, actor_id)
        ready_event = self._event_class(command_id + "-ready", actor_id)
        if route_id:
            self._classify("class-" + route_id, "OBJECT", route_id, "agent")
        manifest_id = "manifest-" + run_id
        self._classify("class-" + manifest_id, "OBJECT", manifest_id, actor_id)
        return self.runtime.schedule_node(command_id=command_id, task_id="task-1", root_run_id="run-root", subtask_id=node_id, child_run_id=run_id, child_grant_id=grant_id, child_classification_assertion_ref="class-" + run_id, event_classification_assertion_ref=create_event, ready_event_classification_assertion_ref=ready_event, route_object_id=route_id, route_classification_assertion_ref="class-" + route_id if route_id else None, manifest_object_id=manifest_id, manifest_classification_assertion_ref="class-" + manifest_id, requested_capability=requested_capability, attempt_reason=attempt_reason, predecessor_attempt_id=predecessor_attempt_id)

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
        self.assertEqual((routed["node-tool"]["schema_version"], routed["node-tool"]["actual_executor_kind"], routed["node-tool"]["execution_source"]), (2, "TOOL", "DETERMINISTIC_RUNTIME"))
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

    def test_escalation_attempts_replay_and_coordinator_continue_downstream(self):
        nodes = self._nodes()
        nodes[1]["dependency_ids"] = ["node-e0"]
        nodes[2]["dependency_ids"] = ["node-e1"]
        self.runtime.create_dag(command_id="cmd-escalation-dag", task_id="task-1", root_run_id="run-root", nodes=nodes)
        self._activate_root()
        for args in (("model-e1", "E1", 1), ("model-e2", "E2", 2)):
            self._model_profile(*args)
        self._schedule("node-e0", "run-e0", "model-agent", "grant-e0", "route-e0")
        for state, command in (("RUNNING", "cmd-e0-running"), ("VERIFYING", "cmd-e0-verifying"), ("SUCCEEDED", "cmd-e0-succeeded")):
            expected = {"RUNNING":"READY", "VERIFYING":"RUNNING", "SUCCEEDED":"VERIFYING"}[state]
            self._advance("run-e0", command, expected, state, "model-agent")
        first = self._schedule("node-e1", "run-e1", "model-agent", "grant-e1", "route-e1", requested_capability="E1")
        self._advance("run-e1", "cmd-e1-running", "READY", "RUNNING", "model-agent")
        self._advance("run-e1", "cmd-e1-waiting", "RUNNING", "WAITING", "model-agent")
        with self.assertRaisesRegex(RuntimeDenied, "SUBTASK_PREVIOUS_ATTEMPT_NOT_TERMINAL_FAILURE"):
            self._schedule("node-e1", "run-e2", "model-agent", "grant-e2", "route-e2", command_id="cmd-e1-attempt-2", requested_capability="E2", attempt_reason="VERIFIER_REQUIRED_ESCALATION", predecessor_attempt_id=first["attempt_id"])
        self._advance("run-e1", "cmd-e1-resumed", "WAITING", "RUNNING", "model-agent")
        self._advance("run-e1", "cmd-e1-escalate-failure", "RUNNING", "FAILED", "model-agent")
        self.assertEqual(self.runtime.replay_subtask("node-e1")["status"], "WAITING")
        second = self._schedule("node-e1", "run-e2", "model-agent", "grant-e2", "route-e2", command_id="cmd-e1-attempt-2", requested_capability="E2", attempt_reason="VERIFIER_REQUIRED_ESCALATION", predecessor_attempt_id=first["attempt_id"])
        self.assertEqual(second["attempt_no"], 2)
        self.assertEqual(self._schedule("node-e1", "run-e2", "model-agent", "grant-e2", "route-e2", command_id="cmd-e1-attempt-2", requested_capability="E2", attempt_reason="VERIFIER_REQUIRED_ESCALATION", predecessor_attempt_id=first["attempt_id"]), second)
        for state, command in (("RUNNING", "cmd-e2-running"), ("VERIFYING", "cmd-e2-verifying"), ("SUCCEEDED", "cmd-e2-succeeded")):
            expected = {"RUNNING":"READY", "VERIFYING":"RUNNING", "SUCCEEDED":"VERIFYING"}[state]
            self._advance("run-e2", command, expected, state, "model-agent")
        projection = self.runtime.replay_subtask("node-e1")
        self.assertEqual(projection["final_outcome"], "SUCCEEDED")
        self.assertEqual([item["requested_capability"] for item in projection["attempts"]], ["E1", "E2"])
        self.assertEqual([item["run_id"] for item in projection["attempts"]], ["run-e1", "run-e2"])
        downstream = self._schedule("node-e2", "run-e2-downstream", "model-agent", "grant-e2-downstream", "route-e2-downstream")
        self.assertEqual(downstream["status"], "READY")
        with self.store._connection() as conn:
            reservations = conn.execute("SELECT COUNT(DISTINCT run_id) FROM budget_reservations WHERE run_id IN ('run-e1','run-e2')").fetchone()[0]
            attempt_count = conn.execute("SELECT COUNT(*) FROM subtask_attempts WHERE subtask_id='node-e1'").fetchone()[0]
        self.assertEqual((reservations, attempt_count), (2, 2))

    def test_escalation_failure_finalizes_once_as_inconclusive(self):
        nodes = self._nodes()
        nodes[1]["dependency_ids"] = []
        self.runtime.create_dag(command_id="cmd-fallback-dag", task_id="task-1", root_run_id="run-root", nodes=nodes)
        self._activate_root()
        for args in (("model-e1", "E1", 1), ("model-e2", "E2", 2)):
            self._model_profile(*args)
        first = self._schedule("node-e1", "run-fail-e1", "model-agent", "grant-fail-e1", "route-fail-e1", requested_capability="E1")
        self._advance("run-fail-e1", "cmd-fail-e1-running", "READY", "RUNNING", "model-agent")
        self._advance("run-fail-e1", "cmd-fail-e1-failed", "RUNNING", "FAILED", "model-agent")
        second = self._schedule("node-e1", "run-fail-e2", "model-agent", "grant-fail-e2", "route-fail-e2", command_id="cmd-fail-e2-attempt", requested_capability="E2", attempt_reason="VERIFIER_REQUIRED_ESCALATION", predecessor_attempt_id=first["attempt_id"])
        self._advance("run-fail-e2", "cmd-fail-e2-running", "READY", "RUNNING", "model-agent")
        self._advance("run-fail-e2", "cmd-fail-e2-failed", "RUNNING", "FAILED", "model-agent")
        final = self.runtime.finalize_subtask(command_id="cmd-e1-inconclusive", task_id="task-1", root_run_id="run-root", subtask_id="node-e1", outcome="INCONCLUSIVE", classification_assertion_ref=self._event_class("cmd-e1-inconclusive", "agent"))
        self.assertEqual(final["final_attempt_id"], second["attempt_id"])
        self.assertEqual(self.runtime.finalize_subtask(command_id="cmd-e1-inconclusive", task_id="task-1", root_run_id="run-root", subtask_id="node-e1", outcome="INCONCLUSIVE", classification_assertion_ref="class-evt-cmd-e1-inconclusive"), final)
        self.assertEqual(self.trace.replay_run("run-root")["status"], "RUNNING")
        projection = self.runtime.replay_subtask("node-e1")
        self.assertEqual((projection["status"], projection["final_outcome"], len(projection["attempts"])), ("FAILED", "INCONCLUSIVE", 2))
        with self.assertRaisesRegex(RuntimeDenied, "SUBTASK_NOT_SCHEDULABLE"):
            self._schedule("node-e1", "run-illegal-third", "model-agent", "grant-illegal-third", "route-illegal-third", command_id="cmd-illegal-third", requested_capability="E2", attempt_reason="RETRY", predecessor_attempt_id=second["attempt_id"])

    def test_unknown_effect_is_reconciled_without_retry_and_compensation_is_independent(self):
        class FakeDispatcher:
            def __init__(self): self.calls = 0
            def dispatch(self, **kwargs):
                self.calls += 1
                if self.calls == 1: raise AmbiguousDispatch("synthetic response loss")
                return {"outcome": "COMMITTED", "receipt_ref": "fake-receipt-2"}

        class FakeAuthorityChannel:
            channel_id = "fake-authoritative"
            authoritative = True
            def __init__(self): self.calls = 0
            def query(self, **kwargs): self.calls += 1; return {"outcome": "COMMITTED", "evidence_ref": "fake-authoritative-evidence-1"}

        self.runtime.create_dag(command_id="cmd-effect-dag", task_id="task-1", root_run_id="run-root", nodes=self._nodes())
        self._activate_root()
        dispatcher = FakeDispatcher()
        reconciliation_channel = FakeAuthorityChannel()
        effects = DeterministicEffectService(self.store, self.authority, self.trace, self.budget, dispatchers={"tool-read": dispatcher}, reconciliation_ports={"fake-authoritative": reconciliation_channel})
        descriptor = {"schema_id": "nexus.tool_descriptor", "schema_version": 1, "tool_id": "tool-read", "version": "1", "input_schema_id": "nexus.object@1.schema.json", "output_schema_id": "nexus.object@1.schema.json", "effect_class": "EXTERNAL_REVERSIBLE", "required_authority": ["FAKE_WRITE"], "required_classifications": ["PUBLIC"], "idempotency_support": True, "reconciliation_capability": "fake-authoritative", "compensation_capability": "fake-compensate", "network_egress": False, "risk_tags": ["synthetic"], "review_status": "APPROVED"}
        effects.register_descriptor(command_id="cmd-effect-descriptor", grant_id="grant-root", task_id="task-1", descriptor=descriptor)
        self._schedule("node-tool", "run-tool", "tool-agent", "grant-tool")
        self._advance("run-tool", "cmd-tool-running", "READY", "RUNNING", "tool-agent")
        digest = self.store.get_object_metadata("input-1")["integrity_hash"]
        effect = {"schema_id": "nexus.effect", "schema_version": 1, "effect_id": "effect-original", "run_id": "run-tool", "tool_id": "tool-read", "action_type": "FAKE_WRITE", "target_ref": "sandbox-target", "payload_integrity_hash": digest, "idempotency_key": "stable-effect-key-1", "grant_id": "grant-tool", "execution_state": "DECLARED", "effect_outcome": "UNDETERMINED", "reconciliation_status": "NOT_REQUIRED"}
        approval = {"schema_id": "nexus.approval_decision", "schema_version": 1, "approval_id": "approve-effect-original", "approver_principal_id": "human-root", "target_type": "FAKE_WRITE", "target_ref": "sandbox-target", "effect_id": "effect-original", "payload_integrity_hash": digest, "decision": "APPROVE", "approved_scope": ["FAKE_WRITE", "sandbox-target"], "policy_version": "1", "issued_at": self._now()}
        self.authority.create_approval(approval, "cmd-approve-original")
        effect["approval_ref"] = "approve-effect-original"
        effects.create_effect(command_id="cmd-effect-create", effect=effect, payload_object_ref="input-1", classification_assertion_ref=self._event_class("cmd-effect-create", "tool-agent"))
        effects.prepare(command_id="cmd-effect-prepare", effect_id="effect-original", classification_assertion_ref=self._event_class("cmd-effect-prepare", "tool-agent"))
        effects.authorize(command_id="cmd-effect-authorize", effect_id="effect-original", classification_assertion_ref=self._event_class("cmd-effect-authorize", "tool-agent"))
        self.runtime.set_mode(command_id="cmd-mode-safe", grant_id="grant-root", task_id="task-1", mode="SAFE", classification_assertion_ref=self._event_class("cmd-mode-safe", "agent"))
        with self.assertRaisesRegex(RuntimeDenied, "RUNTIME_MODE_DENIED"):
            effects.commit(command_id="cmd-effect-safe-denied", effect_id="effect-original", classification_assertion_ref=self._event_class("cmd-effect-safe-denied-outcome", "tool-agent"), start_classification_assertion_ref=self._event_class("cmd-effect-safe-denied-start", "tool-agent"))
        self.runtime.set_mode(command_id="cmd-mode-normal", grant_id="grant-root", task_id="task-1", mode="NORMAL", classification_assertion_ref=self._event_class("cmd-mode-normal", "agent"))
        original = effects.commit(command_id="cmd-effect-commit", effect_id="effect-original", classification_assertion_ref=self._event_class("cmd-effect-commit-outcome", "tool-agent"), start_classification_assertion_ref=self._event_class("cmd-effect-commit-start", "tool-agent"))
        self.assertEqual(original["effect_outcome"], "UNKNOWN")
        self.assertEqual(dispatcher.calls, 1)
        now = datetime.now(timezone.utc)
        for grant_id, actions in (("inspect-reader", ["INSPECT"]), ("inspect-protected-reader", ["INSPECT", "INSPECT_PROTECTED"])):
            self.authority.create_grant({"schema_id":"nexus.delegation_grant","schema_version":1,"grant_id":grant_id,"issued_by":"human-root","granted_to":"agent","task_scope":["task-1"],"resource_scope":["effect:effect-original","approval:approve-effect-original","approval:approve-effect-original:payload-integrity"],"action_scope":actions,"audience_scope":["nexus-inspect"],"issued_at":now.isoformat(),"expires_at":(now+timedelta(days=1)).isoformat(),"status":"ACTIVE","policy_version":"1"}, "cmd-" + grant_id)
        inspector = InspectService(self.store, self.authority)
        effect_view = inspector.effect(grant_id="inspect-reader", task_id="task-1", effect_id="effect-original")
        self.assertEqual((effect_view["execution_state"], effect_view["effect_outcome"], effect_view["reconciliation_status"]), ("FINISHED", "UNKNOWN", "PENDING"))
        self.assertEqual(effect_view["reconciliation_capability"], "fake-authoritative")
        client_effect_view = OperatorClient(self.runtime).inspect_effect(grant_id="inspect-reader", task_id="task-1", effect_id="effect-original")
        self.assertIn("do not retry commit", client_effect_view["display_state"])
        approval_view = inspector.approval(grant_id="inspect-reader", task_id="task-1", approval_id="approve-effect-original")
        self.assertEqual(approval_view["payload_integrity_hash"], "REDACTED")
        with self.assertRaises(AuthorizationDenied):
            inspector.approval(grant_id="inspect-reader", task_id="task-1", approval_id="approve-effect-original", include_payload_hash=True)
        protected_approval = inspector.approval(grant_id="inspect-protected-reader", task_id="task-1", approval_id="approve-effect-original", include_payload_hash=True)
        self.assertEqual(protected_approval["payload_integrity_hash"], digest)
        replay = effects.commit(command_id="cmd-effect-commit", effect_id="effect-original", classification_assertion_ref=self._event_class("cmd-effect-commit-outcome", "tool-agent"), start_classification_assertion_ref=self._event_class("cmd-effect-commit-start", "tool-agent"))
        self.assertEqual(replay, original)
        self.assertEqual(dispatcher.calls, 1)
        resolved = effects.reconcile(command_id="cmd-effect-reconcile", effect_id="effect-original", classification_assertion_ref=self._event_class("cmd-effect-reconcile", "tool-agent"))
        self.assertEqual(resolved["effect_outcome"], "COMMITTED")
        self.assertEqual(effects.reconcile(command_id="cmd-effect-reconcile", effect_id="effect-original", classification_assertion_ref=self._event_class("cmd-effect-reconcile", "tool-agent")), resolved)
        self.assertEqual(reconciliation_channel.calls, 1)
        self._schedule("node-tool-comp", "run-tool-comp", "tool-agent", "grant-tool-comp")
        self._advance("run-tool-comp", "cmd-tool-comp-running", "READY", "RUNNING", "tool-agent")
        compensation = {**effect, "effect_id": "effect-compensation", "run_id": "run-tool-comp", "grant_id": "grant-tool-comp", "idempotency_key": "stable-effect-key-2", "approval_ref": "approve-effect-compensation"}
        compensation_approval = {**approval, "approval_id": "approve-effect-compensation", "effect_id": "effect-compensation"}
        self.authority.create_approval(compensation_approval, "cmd-approve-compensation")
        effects.create_effect(command_id="cmd-compensation-create", effect=compensation, payload_object_ref="input-1", classification_assertion_ref=self._event_class("cmd-compensation-create", "tool-agent"), compensates_effect_id="effect-original")
        effects.prepare(command_id="cmd-compensation-prepare", effect_id="effect-compensation", classification_assertion_ref=self._event_class("cmd-compensation-prepare", "tool-agent"))
        effects.authorize(command_id="cmd-compensation-authorize", effect_id="effect-compensation", classification_assertion_ref=self._event_class("cmd-compensation-authorize", "tool-agent"))
        self.assertEqual(effects.commit(command_id="cmd-compensation-commit", effect_id="effect-compensation", classification_assertion_ref=self._event_class("cmd-compensation-commit-outcome", "tool-agent"), start_classification_assertion_ref=self._event_class("cmd-compensation-commit-start", "tool-agent"))["effect_outcome"], "COMMITTED")
        with self.store._connection() as conn:
            facts = {row["effect_id"]: row["effect_outcome"] for row in conn.execute("SELECT effect_id,effect_outcome FROM effects")}
            relation = conn.execute("SELECT relation_type FROM effect_relations WHERE from_effect_id='effect-compensation' AND to_effect_id='effect-original'").fetchone()
            budget = conn.execute("SELECT reserved,consumed,tool_calls_reserved,tool_calls_consumed FROM budget_accounts WHERE account_id='budget-1'").fetchone()
        self.assertEqual(facts, {"effect-original": "COMMITTED", "effect-compensation": "COMMITTED"})
        self.assertEqual(relation["relation_type"], "COMPENSATES")
        self.assertEqual(tuple(budget), (0, 4, 0, 2))

    def test_effect_commit_revalidates_payload_bound_approval_and_revocation(self):
        class NeverDispatch:
            calls = 0
            def dispatch(self, **kwargs): self.calls += 1; return {"outcome": "COMMITTED", "receipt_ref": "should-not-run"}

        self.runtime.create_dag(command_id="cmd-deny-effect-dag", task_id="task-1", root_run_id="run-root", nodes=self._nodes())
        self._activate_root()
        effects = DeterministicEffectService(self.store, self.authority, self.trace, self.budget, dispatchers={"tool-read": NeverDispatch()})
        descriptor = {"schema_id": "nexus.tool_descriptor", "schema_version": 1, "tool_id": "tool-read", "version": "1", "input_schema_id": "nexus.object@1.schema.json", "output_schema_id": "nexus.object@1.schema.json", "effect_class": "EXTERNAL_REVERSIBLE", "required_authority": ["FAKE_WRITE"], "required_classifications": ["PUBLIC"], "idempotency_support": True, "reconciliation_capability": "fake-authoritative", "compensation_capability": "fake-compensate", "network_egress": False, "risk_tags": [], "review_status": "APPROVED"}
        effects.register_descriptor(command_id="cmd-deny-effect-descriptor", grant_id="grant-root", task_id="task-1", descriptor=descriptor)
        self._schedule("node-tool", "run-tool", "tool-agent", "grant-tool")
        self._advance("run-tool", "cmd-deny-effect-running", "READY", "RUNNING", "tool-agent")
        payload_hash = self.store.get_object_metadata("input-1")["integrity_hash"]
        effect = {"schema_id": "nexus.effect", "schema_version": 1, "effect_id": "effect-denied", "run_id": "run-tool", "tool_id": "tool-read", "action_type": "FAKE_WRITE", "target_ref": "sandbox-target", "payload_integrity_hash": payload_hash, "idempotency_key": "stable-effect-denied", "grant_id": "grant-tool", "execution_state": "DECLARED", "effect_outcome": "UNDETERMINED", "reconciliation_status": "NOT_REQUIRED", "approval_ref": "approval-wrong-payload"}
        self.authority.create_approval({"schema_id": "nexus.approval_decision", "schema_version": 1, "approval_id": "approval-wrong-payload", "approver_principal_id": "human-root", "target_type": "FAKE_WRITE", "target_ref": "sandbox-target", "effect_id": "effect-denied", "payload_integrity_hash": "0" * 64, "decision": "APPROVE", "approved_scope": ["FAKE_WRITE", "sandbox-target"], "policy_version": "1", "issued_at": self._now()}, "cmd-approval-wrong-payload")
        effects.create_effect(command_id="cmd-create-denied-effect", effect=effect, payload_object_ref="input-1", classification_assertion_ref=self._event_class("cmd-create-denied-effect", "tool-agent"))
        effects.prepare(command_id="cmd-denied-effect-prepare", effect_id="effect-denied", classification_assertion_ref=self._event_class("cmd-denied-effect-prepare", "tool-agent"))
        effects.authorize(command_id="cmd-denied-effect-authorize", effect_id="effect-denied", classification_assertion_ref=self._event_class("cmd-denied-effect-authorize", "tool-agent"))
        with self.assertRaisesRegex(ApprovalDenied, "APPROVAL_PAYLOAD_HASH_MISMATCH"):
            effects.commit(command_id="cmd-denied-effect-commit", effect_id="effect-denied", classification_assertion_ref=self._event_class("cmd-denied-effect-commit-outcome", "tool-agent"), start_classification_assertion_ref=self._event_class("cmd-denied-effect-commit-start", "tool-agent"))
        self.assertEqual(effects._get("effect-denied")["execution_state"], "AUTHORIZED")
        self.authority.revoke_grant("grant-root", "cmd-deny-effect-revoke-parent")
        with self.assertRaises(InvalidDelegation):
            effects.commit(command_id="cmd-denied-effect-after-revoke", effect_id="effect-denied", classification_assertion_ref=self._event_class("cmd-denied-effect-after-revoke-outcome", "tool-agent"), start_classification_assertion_ref=self._event_class("cmd-denied-effect-after-revoke-start", "tool-agent"))
        self.assertEqual(effects.dispatchers["tool-read"].calls, 0)
        self.assertEqual(effects._get("effect-denied")["execution_state"], "AUTHORIZED")

    def _root(self):
        with self.store._connection() as conn:
            return conn.execute("SELECT * FROM runs WHERE run_id='run-root'").fetchone()


if __name__ == "__main__":
    unittest.main()
