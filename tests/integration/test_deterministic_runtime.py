import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

from adapters.storage import ObjectStore
from adapters.client.operator import OperatorClient
from kernel.authority import AuthorityService
from kernel.authority.errors import ApprovalDenied, AuthorizationDenied, InvalidDelegation
from kernel.budget import BudgetService
from kernel.run import TraceAdmissionDenied, TraceRuntime
from kernel.run.errors import InvalidRunTransition
from kernel.effect import AmbiguousDispatch, DeterministicEffectService
from kernel.runtime import DeterministicRuntime
from kernel.runtime.inspect import InspectService
from kernel.runtime.errors import RuntimeDenied
from kernel.object.errors import CommandConflict


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

    def _child_grant(self, run_id, principal_id, grant_id, expires_at=None):
        with self.store._connection() as conn:
            if conn.execute("SELECT 1 FROM delegation_grants WHERE grant_id=?", (grant_id,)).fetchone():
                return
        now = datetime.now(timezone.utc)
        self.authority.create_grant({"schema_id": "nexus.delegation_grant", "schema_version": 1, "grant_id": grant_id, "parent_grant_id": "grant-root", "issued_by": "agent", "granted_to": principal_id, "task_scope": ["task-1"], "resource_scope": [run_id, "sandbox-target", "tool-read"], "action_scope": ["RUN_CREATE", "RUN_TRANSITION", "TRACE_APPEND", "OBJECT_WRITE", "TOOL_READ", "EFFECT_PREPARE", "EFFECT_COMMIT", "EFFECT_RECONCILE", "EFFECT_COMPENSATE", "FAKE_WRITE"], "audience_scope": ["nexus-runtime"], "issued_at": now.isoformat(), "expires_at": (expires_at or (now + timedelta(days=30))).isoformat(), "status": "ACTIVE", "policy_version": "1"}, "cmd-" + grant_id)

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
        cancelled_event = self._event_class(command_id + "-setup-cancel", actor_id)
        return self.runtime.schedule_node(command_id=command_id, task_id="task-1", root_run_id="run-root", subtask_id=node_id, child_run_id=run_id, child_grant_id=grant_id, child_classification_assertion_ref="class-" + run_id, event_classification_assertion_ref=create_event, ready_event_classification_assertion_ref=ready_event, cancelled_event_classification_assertion_ref=cancelled_event, route_object_id=route_id, route_classification_assertion_ref="class-" + route_id if route_id else None, manifest_object_id=manifest_id, manifest_classification_assertion_ref="class-" + manifest_id, requested_capability=requested_capability, attempt_reason=attempt_reason, predecessor_attempt_id=predecessor_attempt_id)

    def _advance(self, run_id, command_id, expected, target, actor_id):
        classification = self._event_class(command_id, actor_id)
        return self.trace.transition_run(command_id=command_id, run_id=run_id, expected_state=expected, next_state=target, classification_assertion_ref=classification)

    def test_kernel_recovery_identity_is_denied_by_normal_authority_apis(self):
        now = datetime.now(timezone.utc)
        base = {"schema_id": "nexus.delegation_grant", "schema_version": 1,
            "grant_id": "grant-reserved-recovery", "parent_grant_id": "grant-root",
            "issued_by": "agent", "granted_to": "nexus-core-recovery", "task_scope": ["task-1"],
            "resource_scope": ["run-root"], "action_scope": ["RUN_CREATE"], "audience_scope": ["nexus-runtime"],
            "issued_at": now.isoformat(), "expires_at": (now + timedelta(days=1)).isoformat(),
            "status": "ACTIVE", "policy_version": "1"}
        with self.assertRaisesRegex(InvalidDelegation, "KERNEL_RECOVERY_IDENTITY_RESERVED"):
            self.authority.create_grant(base, "cmd-grant-to-recovery")
        with self.assertRaisesRegex(InvalidDelegation, "KERNEL_RECOVERY_IDENTITY_RESERVED"):
            self.authority.create_grant({**base, "grant_id": "grant-issued-by-recovery", "issued_by": "nexus-core-recovery", "granted_to": "model-agent"}, "cmd-grant-from-recovery")
        policy = dict(self.authority.policy)
        policy["trust_anchors"] = [*policy["trust_anchors"], "nexus-core-recovery"]
        configured_authority = AuthorityService(self.store, policy)
        with self.assertRaisesRegex(InvalidDelegation, "KERNEL_RECOVERY_IDENTITY_RESERVED"):
            configured_authority.register_trust_anchor({"schema_id": "nexus.trust_anchor", "schema_version": 1,
                "anchor_id": "anchor-recovery", "principal_id": "nexus-core-recovery", "policy_ref": "1"}, "cmd-anchor-recovery")
        with self.assertRaisesRegex(TraceAdmissionDenied, "KERNEL_RECOVERY_IDENTITY_RESERVED"):
            self.trace.create_task({"schema_id": "nexus.task", "schema_version": 1, "task_id": "task-recovery-requester",
                "requester_id": "nexus-core-recovery", "status": "CREATED", "created_at": self._now(),
                "command_id": "cmd-task-recovery-requester"})
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM delegation_grants WHERE issued_by='nexus-core-recovery' OR granted_to='nexus-core-recovery'").fetchone()[0], 0)
            self.assertIsNone(conn.execute("SELECT 1 FROM trust_anchors WHERE principal_id='nexus-core-recovery'").fetchone())
            self.assertIsNone(conn.execute("SELECT 1 FROM tasks WHERE requester_id='nexus-core-recovery'").fetchone())

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

    def test_schedule_retry_after_ready_transition_interruption_records_authoritative_ready_projection(self):
        self.runtime.create_dag(command_id="cmd-interrupted-dag", task_id="task-1", root_run_id="run-root", nodes=self._nodes())
        self._activate_root()
        self._model_profile("model-e0", "E0", 1)
        args = {
            "command_id": "cmd-interrupted-schedule", "task_id": "task-1", "root_run_id": "run-root",
            "subtask_id": "node-e0", "child_run_id": "run-e0", "child_grant_id": "grant-interrupted",
            "child_classification_assertion_ref": "class-run-e0",
            "event_classification_assertion_ref": "class-evt-cmd-interrupted-schedule-create-run",
            "ready_event_classification_assertion_ref": "class-evt-cmd-interrupted-schedule-ready",
            "cancelled_event_classification_assertion_ref": "class-evt-cmd-interrupted-schedule-setup-cancel",
            "route_object_id": "route-e0", "route_classification_assertion_ref": "class-route-e0",
            "manifest_object_id": "manifest-run-e0", "manifest_classification_assertion_ref": "class-manifest-run-e0",
        }
        self._child_grant("run-e0", "model-agent", "grant-interrupted")
        self._classify("class-run-e0", "RUN", "run-e0", "model-agent")
        self._classify("class-evt-cmd-interrupted-schedule-create-run", "TRACE_EVENT", "evt-cmd-interrupted-schedule-create-run", "model-agent")
        self._classify("class-evt-cmd-interrupted-schedule-ready", "TRACE_EVENT", "evt-cmd-interrupted-schedule-ready", "model-agent")
        self._classify("class-evt-cmd-interrupted-schedule-setup-cancel", "TRACE_EVENT", "evt-cmd-interrupted-schedule-setup-cancel", "model-agent")
        self._classify("class-route-e0", "OBJECT", "route-e0", "agent")
        self._classify("class-manifest-run-e0", "OBJECT", "manifest-run-e0", "model-agent")
        original_transition = self.trace.transition_run
        failed = False

        def fail_before_ready(**kwargs):
            nonlocal failed
            if kwargs["command_id"] == "cmd-interrupted-schedule-ready" and not failed:
                failed = True
                raise RuntimeError("injected interruption before READY transition")
            return original_transition(**kwargs)

        with patch.object(self.trace, "transition_run", side_effect=fail_before_ready):
            with self.assertRaisesRegex(RuntimeError, "injected interruption"):
                self.runtime.schedule_node(**args)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM runs WHERE run_id='run-e0'").fetchone()[0], "CREATED")
            self.assertEqual(conn.execute("SELECT outcome FROM subtask_attempts WHERE run_id='run-e0'").fetchone()[0], "CREATED")
            self.assertEqual(conn.execute("SELECT state FROM budget_reservations WHERE run_id='run-e0'").fetchone()[0], "RESERVED")
            self.assertIsNone(conn.execute("SELECT 1 FROM command_ledger WHERE command_id=?", (args["command_id"],)).fetchone())
            counts_before = tuple(conn.execute("SELECT (SELECT COUNT(*) FROM budget_reservations WHERE run_id=?),(SELECT COUNT(*) FROM subtask_attempts WHERE run_id=?),(SELECT COUNT(*) FROM route_decisions WHERE subtask_id=?)", (args["child_run_id"], args["child_run_id"], args["subtask_id"])).fetchone())

        recovered = self.runtime.schedule_node(**args)
        self.assertEqual(recovered["status"], "READY")
        self.assertEqual(self.trace.replay_run(args["child_run_id"])["status"], "READY")
        self.assertEqual(self.runtime.replay_subtask(args["subtask_id"])["status"], "READY")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT outcome FROM subtask_attempts WHERE run_id=?", (args["child_run_id"],)).fetchone()[0], "READY")
            stored = json.loads(conn.execute("SELECT result_json FROM command_ledger WHERE command_id=?", (args["command_id"],)).fetchone()[0])
            counts_after = tuple(conn.execute("SELECT (SELECT COUNT(*) FROM budget_reservations WHERE run_id=?),(SELECT COUNT(*) FROM subtask_attempts WHERE run_id=?),(SELECT COUNT(*) FROM route_decisions WHERE subtask_id=?)", (args["child_run_id"], args["child_run_id"], args["subtask_id"])).fetchone())
        self.assertEqual(stored, recovered)
        self.assertEqual(counts_after, counts_before)
        self.assertEqual(self.runtime.schedule_node(**args), recovered)

    def test_half_registered_attempt_cannot_be_adopted_by_another_command(self):
        prepared = self._prepare_single_schedule(command_id="cmd-half-attempt-owner")
        args = dict(command_id="cmd-half-attempt-owner", task_id="task-1", root_run_id="run-root", subtask_id=prepared[0], child_run_id=prepared[1], child_grant_id=prepared[3], route_object_id=prepared[4])
        self._child_grant(prepared[1], prepared[2], prepared[3])
        self._classify("class-" + prepared[1], "RUN", prepared[1], prepared[2])
        create_command = args["command_id"] + "-create-run"
        args.update(child_classification_assertion_ref="class-" + prepared[1], event_classification_assertion_ref=self._event_class(create_command, prepared[2]), ready_event_classification_assertion_ref=self._event_class(args["command_id"] + "-ready", prepared[2]), cancelled_event_classification_assertion_ref=self._event_class(args["command_id"] + "-setup-cancel", prepared[2]), route_classification_assertion_ref="class-" + prepared[4], manifest_object_id="manifest-" + prepared[1])
        self._classify("class-" + prepared[4], "OBJECT", prepared[4], "agent")
        self._classify(args["manifest_object_id"], "OBJECT", args["manifest_object_id"], prepared[2])
        args["manifest_classification_assertion_ref"] = args["manifest_object_id"]
        original_ready = self.trace.transition_run

        def interrupt_ready(**kwargs):
            if kwargs.get("command_id") == args["command_id"] + "-ready":
                raise RuntimeError("simulated response interruption before READY")
            return original_ready(**kwargs)

        self.trace.transition_run = interrupt_ready
        with self.assertRaisesRegex(RuntimeError, "simulated response interruption"):
            self.runtime.schedule_node(**args)
        self.trace.transition_run = original_ready
        with self.store._connection() as conn:
            attempt = conn.execute("SELECT command_id,schedule_request_hash,outcome FROM subtask_attempts WHERE run_id=?", (args["child_run_id"],)).fetchone()
            self.assertEqual(attempt["command_id"], args["command_id"])
            self.assertIsNotNone(attempt["schedule_request_hash"])
            self.assertEqual(attempt["outcome"], "CREATED")
        adopted = {**args, "command_id": "cmd-different-attempt-owner"}
        with self.assertRaisesRegex(RuntimeDenied, "SCHEDULE_ATTEMPT_BINDING_MISMATCH"):
            self.runtime.schedule_node(**adopted)
        recovered = self.runtime.schedule_node(**args)
        self.assertEqual(recovered["status"], "READY")
        self.assertEqual(self.runtime.schedule_node(**args), recovered)

    def test_historical_schedule_command_replays_old_request_shape(self):
        args = {
            "command_id": "cmd-historical-schedule", "task_id": "task-1", "root_run_id": "run-root",
            "subtask_id": "node-e0", "child_run_id": "run-e0", "child_grant_id": "grant-historical",
            "child_classification_assertion_ref": "class-run-e0",
            "event_classification_assertion_ref": "class-evt-cmd-historical-schedule-create-run",
            "ready_event_classification_assertion_ref": "class-evt-cmd-historical-schedule-ready",
            "cancelled_event_classification_assertion_ref": "class-evt-cmd-historical-schedule-setup-cancel",
            "route_object_id": "route-e0", "route_classification_assertion_ref": "class-route-e0",
            "manifest_object_id": "manifest-run-e0", "manifest_classification_assertion_ref": "class-manifest-run-e0",
            "requested_capability": None, "attempt_reason": "INITIAL", "predecessor_attempt_id": None,
        }
        self.runtime.create_dag(command_id="cmd-historical-dag", task_id="task-1", root_run_id="run-root", nodes=self._nodes())
        self._activate_root()
        self._model_profile("model-e0", "E0", 1)
        self._child_grant("run-e0", "model-agent", "grant-historical")
        for assertion, subject_type, subject_ref, actor in (
            ("class-run-e0", "RUN", "run-e0", "model-agent"),
            ("class-evt-cmd-historical-schedule-create-run", "TRACE_EVENT", "evt-cmd-historical-schedule-create-run", "model-agent"),
            ("class-evt-cmd-historical-schedule-ready", "TRACE_EVENT", "evt-cmd-historical-schedule-ready", "model-agent"),
            ("class-evt-cmd-historical-schedule-setup-cancel", "TRACE_EVENT", "evt-cmd-historical-schedule-setup-cancel", "model-agent"),
            ("class-route-e0", "OBJECT", "route-e0", "agent"),
            ("class-manifest-run-e0", "OBJECT", "manifest-run-e0", "model-agent"),
        ):
            self._classify(assertion, subject_type, subject_ref, actor)
        original = self.runtime.schedule_node(**args)
        # v1 is the actual pre-T10 deterministic scheduler request recorded in
        # 2eeead0f: all original authority/classification/route/manifest refs,
        # before the three multi-attempt fields existed.
        v1_fields = ("task_id", "root_run_id", "subtask_id", "child_run_id", "child_grant_id", "child_classification_assertion_ref", "event_classification_assertion_ref", "ready_event_classification_assertion_ref", "route_object_id", "route_classification_assertion_ref", "manifest_object_id", "manifest_classification_assertion_ref")
        v1_request = {key: args[key] for key in v1_fields}
        v1_hash = self.store._request_hash("schedule_subtask", v1_request)
        # v2 is the actual T10 multi-attempt request shape present at release
        # 0cb9d1ed: v1 plus capability/reason/predecessor.
        v2_request = {key: args[key] for key in (*v1_fields, "requested_capability", "attempt_reason", "predecessor_attempt_id")}
        v2_hash = self.store._request_hash("schedule_subtask", v2_request)
        with self.store._connection() as conn:
            # Emulate an immutable command row written by the pre-change binary.
            # Historical data is not rewritten in production; the disposable DB
            # fixture is rebuilt here to represent that older committed shape.
            conn.execute("DROP TRIGGER command_ledger_no_update")
            conn.execute("UPDATE command_ledger SET request_hash=? WHERE command_id=?", (v1_hash, args["command_id"]))
            self.assertEqual(conn.execute("SELECT request_hash FROM command_ledger WHERE command_id=?", (args["command_id"],)).fetchone()[0], v1_hash)
            before = tuple(conn.execute(
                "SELECT (SELECT COUNT(*) FROM budget_reservations WHERE run_id=?),(SELECT COUNT(*) FROM runs WHERE run_id=?),(SELECT COUNT(*) FROM subtask_attempts WHERE run_id=?),(SELECT COUNT(*) FROM route_decisions WHERE subtask_id=?)",
                (args["child_run_id"], args["child_run_id"], args["child_run_id"], args["subtask_id"]),
            ).fetchone())

        # This is the API shape used before the cancellation assertion was added.
        legacy_args = {key: value for key, value in args.items() if key != "cancelled_event_classification_assertion_ref"}
        replayed = self.runtime.schedule_node(**legacy_args)
        self.assertEqual(replayed, original)
        changed_legacy_args = {**legacy_args, "route_object_id": "different-route"}
        with self.assertRaisesRegex(CommandConflict, "COMMAND_CONFLICT"):
            self.runtime.schedule_node(**changed_legacy_args)
        non_default_v2_args = {**legacy_args, "requested_capability": "E1"}
        with self.assertRaisesRegex(CommandConflict, "COMMAND_CONFLICT"):
            self.runtime.schedule_node(**non_default_v2_args)

        # Also accept the actual v2 full request shape, but no changed fields.
        with self.store._connection() as conn:
            conn.execute("UPDATE command_ledger SET request_hash=? WHERE command_id=?", (v2_hash, args["command_id"]))
        self.assertEqual(self.runtime.schedule_node(**legacy_args), original)
        changed_full_args = {**legacy_args, "manifest_object_id": "different-manifest"}
        with self.assertRaisesRegex(CommandConflict, "COMMAND_CONFLICT"):
            self.runtime.schedule_node(**changed_full_args)

        with self.store._connection() as conn:
            conn.execute("UPDATE command_ledger SET request_hash=? WHERE command_id=?", (v1_hash, args["command_id"]))

        # Reopen the persistent root and ensure the old request remains replayable.
        data_root = Path(self.temp.name) / "data"
        self.store.close()
        self.store = ObjectStore(data_root)
        self.addCleanup(self.store.close)
        self.authority = AuthorityService(self.store, self.policy)
        self.budget = BudgetService(self.store)
        self.trace = TraceRuntime(self.store, self.authority)
        self.runtime = DeterministicRuntime(self.store, self.authority, self.budget, self.trace)
        reopened_replay = self.runtime.schedule_node(**legacy_args)
        self.assertEqual(reopened_replay, original)

        new_args = {**args, "command_id": "cmd-new-schedule-without-cancel-class"}
        new_args.pop("cancelled_event_classification_assertion_ref")
        with self.assertRaisesRegex(RuntimeDenied, "SCHEDULE_CANCEL_CLASSIFICATION_REQUIRED"):
            self.runtime.schedule_node(**new_args)

        with self.store._connection() as conn:
            counts = tuple(conn.execute(
                "SELECT (SELECT COUNT(*) FROM budget_reservations WHERE run_id=?),(SELECT COUNT(*) FROM runs WHERE run_id=?),(SELECT COUNT(*) FROM subtask_attempts WHERE run_id=?),(SELECT COUNT(*) FROM route_decisions WHERE subtask_id=?)",
                (args["child_run_id"], args["child_run_id"], args["child_run_id"], args["subtask_id"]),
            ).fetchone())
        self.assertEqual(original["status"], "READY")
        self.assertEqual(counts, before)
        self.assertEqual(self.runtime.schedule_node(**legacy_args), original)

    def _prepare_single_schedule(self, *, command_id="cmd-setup-failure", run_id="run-e0"):
        self.runtime.create_dag(command_id=command_id + "-dag", task_id="task-1", root_run_id="run-root", nodes=self._nodes())
        self._activate_root()
        self._model_profile("model-e0", "E0", 1)
        return ("node-e0", run_id, "model-agent", "grant-e0", "route-" + run_id)

    def _leave_interrupted_compensation_intent(self, command_id="cmd-recovery-negative"):
        args = self._prepare_single_schedule(command_id=command_id)
        original_insert_event = self.trace._insert_event
        original_recovery = self.trace._cancel_created_run_for_recovery
        captured = {}

        def reject_manifest(**kwargs):
            raise RuntimeDenied("INJECTED_SETUP_REJECTION")

        def interrupt_recovery_trace(conn, event):
            if event["actor_id"] == "nexus-core-recovery":
                raise RuntimeError("injected recovery trace interruption")
            return original_insert_event(conn, event)

        def capture_recovery_request(**kwargs):
            captured.update(kwargs)
            return original_recovery(**kwargs)

        with patch.object(self.runtime, "bind_manifest", side_effect=reject_manifest), \
             patch.object(self.trace, "_insert_event", side_effect=interrupt_recovery_trace), \
             patch.object(self.trace, "_cancel_created_run_for_recovery", side_effect=capture_recovery_request):
            with self.assertRaisesRegex(RuntimeError, "SCHEDULE_SETUP_COMPENSATION_FAILED"):
                self._schedule(*args, command_id=command_id)
        self.assertTrue(captured)
        return args, captured

    def test_permanent_failure_after_reserve_before_run_create_releases_budget_with_disposition(self):
        args = self._prepare_single_schedule()
        with patch.object(self.trace, "create_run", side_effect=TraceAdmissionDenied("INJECTED_CREATE_REJECTION")):
            with self.assertRaisesRegex(RuntimeDenied, "SCHEDULE_SETUP_COMPENSATED"):
                self._schedule(*args, command_id="cmd-setup-failure")
        with self.store._connection() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM runs WHERE run_id='run-e0'").fetchone())
            reservation = conn.execute("SELECT state FROM budget_reservations WHERE run_id='run-e0'").fetchone()
            self.assertEqual(reservation["state"], "RELEASED")
            disposition = json.loads(conn.execute("SELECT result_json FROM command_ledger WHERE command_id='cmd-setup-failure'").fetchone()[0])
            self.assertEqual(disposition["status"], "SETUP_FAILED")
            self.assertEqual(conn.execute("SELECT action FROM budget_ledger WHERE reservation_id=(SELECT reservation_id FROM budget_reservations WHERE run_id='run-e0') ORDER BY ledger_seq", ()).fetchall()[0][0], "RESERVED")
            self.assertEqual(conn.execute("SELECT action FROM budget_ledger WHERE reservation_id=(SELECT reservation_id FROM budget_reservations WHERE run_id='run-e0') ORDER BY ledger_seq", ()).fetchall()[1][0], "RELEASED")
        with self.assertRaisesRegex(RuntimeDenied, "SCHEDULE_SETUP_COMPENSATED"):
            self._schedule(*args, command_id="cmd-setup-failure")

    def test_setup_compensation_intent_resumes_after_root_state_change_and_reopen(self):
        args = {
            "command_id": "cmd-compensation-root-change", "task_id": "task-1", "root_run_id": "run-root",
            "subtask_id": "node-e0", "child_run_id": "run-e0", "child_grant_id": "grant-compensation-root-change",
            "child_classification_assertion_ref": "class-run-e0",
            "event_classification_assertion_ref": "class-evt-cmd-compensation-root-change-create-run",
            "ready_event_classification_assertion_ref": "class-evt-cmd-compensation-root-change-ready",
            "cancelled_event_classification_assertion_ref": "class-evt-cmd-compensation-root-change-setup-cancel",
            "route_object_id": "route-e0", "route_classification_assertion_ref": "class-route-e0",
            "manifest_object_id": "manifest-run-e0", "manifest_classification_assertion_ref": "class-manifest-run-e0",
        }
        self.runtime.create_dag(command_id="cmd-compensation-root-change-dag", task_id="task-1", root_run_id="run-root", nodes=self._nodes())
        self._activate_root()
        self._model_profile("model-e0", "E0", 1)
        self._child_grant("run-e0", "model-agent", "grant-compensation-root-change")
        for assertion, subject_type, subject_ref, actor in (
            ("class-run-e0", "RUN", "run-e0", "model-agent"),
            ("class-evt-cmd-compensation-root-change-create-run", "TRACE_EVENT", "evt-cmd-compensation-root-change-create-run", "model-agent"),
            ("class-evt-cmd-compensation-root-change-ready", "TRACE_EVENT", "evt-cmd-compensation-root-change-ready", "model-agent"),
            ("class-evt-cmd-compensation-root-change-setup-cancel", "TRACE_EVENT", "evt-cmd-compensation-root-change-setup-cancel", "model-agent"),
            ("class-route-e0", "OBJECT", "route-e0", "agent"),
            ("class-manifest-run-e0", "OBJECT", "manifest-run-e0", "model-agent"),
        ):
            self._classify(assertion, subject_type, subject_ref, actor)
        original_insert_event = self.trace._insert_event

        def reject_manifest(**kwargs):
            raise RuntimeDenied("INJECTED_SETUP_REJECTION")

        def interrupt_recovery_trace(conn, event):
            if event["actor_id"] == "nexus-core-recovery":
                raise RuntimeError("injected recovery trace interruption")
            return original_insert_event(conn, event)

        with patch.object(self.runtime, "bind_manifest", side_effect=reject_manifest), patch.object(self.trace, "_insert_event", side_effect=interrupt_recovery_trace):
            with self.assertRaisesRegex(RuntimeError, "SCHEDULE_SETUP_COMPENSATION_FAILED"):
                self.runtime.schedule_node(**args)
        with self.store._connection() as conn:
            self.assertIsNotNone(conn.execute("SELECT 1 FROM command_ledger WHERE command_id=?", (args["command_id"] + "-setup-intent",)).fetchone())
            self.assertIsNone(conn.execute("SELECT 1 FROM command_ledger WHERE command_id=?", (args["command_id"],)).fetchone())
            self.assertEqual(conn.execute("SELECT status FROM runs WHERE run_id='run-e0'").fetchone()[0], "CREATED")
            self.assertEqual(conn.execute("SELECT state FROM budget_reservations WHERE run_id='run-e0'").fetchone()[0], "RESERVED")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM classification_assertions WHERE actor_id='nexus-core-recovery'").fetchone()[0], 0)

        # A legal root transition changes forward eligibility after the intent exists.
        self._advance("run-root", "cmd-compensation-root-failed", "RUNNING", "FAILED", "agent")
        data_root = Path(self.temp.name) / "data"
        self.store.close()
        self.store = ObjectStore(data_root)
        self.addCleanup(self.store.close)
        self.authority = AuthorityService(self.store, self.policy)
        self.budget = BudgetService(self.store)
        self.trace = TraceRuntime(self.store, self.authority)
        self.runtime = DeterministicRuntime(self.store, self.authority, self.budget, self.trace)
        with self.assertRaisesRegex(RuntimeDenied, "SCHEDULE_SETUP_COMPENSATED"):
            self.runtime.schedule_node(**args)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM runs WHERE run_id='run-e0'").fetchone()[0], "CANCELLED")
            self.assertEqual(conn.execute("SELECT state FROM budget_reservations WHERE run_id='run-e0'").fetchone()[0], "RELEASED")
            disposition = json.loads(conn.execute("SELECT result_json FROM command_ledger WHERE command_id=?", (args["command_id"],)).fetchone()[0])
            self.assertEqual(disposition["status"], "SETUP_FAILED")

    def test_kernel_recovery_cleanup_converges_after_child_grant_revocation_or_expiry(self):
        self.runtime.create_dag(command_id="cmd-authority-compensation-dag", task_id="task-1", root_run_id="run-root", nodes=self._nodes())
        self._activate_root()
        self._model_profile("model-e0", "E0", 1)
        self._model_profile("model-e1", "E1", 2)
        original_insert_event = self.trace._insert_event

        for node_id, run_id, grant_id, expires_after in (
            ("node-e0", "run-e0", "grant-revoke-compensation", 30),
            ("node-e1", "run-e1", "grant-expire-compensation", 2),
        ):
            command_id = "schedule-" + grant_id
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_after)
            self._child_grant(run_id, "model-agent", grant_id, expires_at=expires_at)
            self._classify("class-" + run_id, "RUN", run_id, "model-agent")
            route_id = "route-" + run_id
            manifest_id = "manifest-" + run_id
            self._classify("class-" + route_id, "OBJECT", route_id, "agent")
            self._classify("class-" + manifest_id, "OBJECT", manifest_id, "model-agent")
            for suffix in ("create-run", "ready", "setup-cancel"):
                self._event_class(command_id + "-" + suffix, "model-agent")
            args = {
                "command_id": command_id, "task_id": "task-1", "root_run_id": "run-root", "subtask_id": node_id,
                "child_run_id": run_id, "child_grant_id": grant_id,
                "child_classification_assertion_ref": "class-" + run_id,
                "event_classification_assertion_ref": "class-evt-" + command_id + "-create-run",
                "ready_event_classification_assertion_ref": "class-evt-" + command_id + "-ready",
                "cancelled_event_classification_assertion_ref": "class-evt-" + command_id + "-setup-cancel",
                "route_object_id": route_id, "route_classification_assertion_ref": "class-" + route_id,
                "manifest_object_id": manifest_id, "manifest_classification_assertion_ref": "class-" + manifest_id,
            }

            def reject_manifest(**kwargs):
                raise RuntimeDenied("INJECTED_SETUP_REJECTION")

            def interrupt_recovery_trace(conn, event):
                if event["actor_id"] == "nexus-core-recovery":
                    raise RuntimeError("injected recovery trace interruption")
                return original_insert_event(conn, event)

            with patch.object(self.runtime, "bind_manifest", side_effect=reject_manifest), patch.object(self.trace, "_insert_event", side_effect=interrupt_recovery_trace):
                with self.assertRaisesRegex(RuntimeError, "SCHEDULE_SETUP_COMPENSATION_FAILED"):
                    self.runtime.schedule_node(**args)
            with self.store._connection() as conn:
                intent = conn.execute("SELECT result_json FROM command_ledger WHERE command_id=?", (command_id + "-setup-intent",)).fetchone()
                self.assertIsNotNone(intent)
                reservation_id = json.loads(intent["result_json"])["reservation_id"]
                self.assertIsNone(conn.execute("SELECT 1 FROM trace_events WHERE event_id=?", ("evt-" + command_id + "-setup-core-cancel",)).fetchone())
                self.assertIsNone(conn.execute("SELECT 1 FROM classification_assertions WHERE assertion_id=?", ("class-evt-" + command_id + "-setup-core-cancel",)).fetchone())
            if expires_after == 30:
                self.authority.revoke_grant(grant_id, command_id + "-revoke")
            else:
                future = datetime.now(timezone.utc) + timedelta(minutes=1)
                with patch("kernel.authority.service._now", return_value=future):
                    with self.assertRaises(InvalidDelegation):
                        self.authority.validate_delegation_chain(grant_id)
            data_root = Path(self.temp.name) / "data"
            self.store.close()
            self.store = ObjectStore(data_root)
            self.addCleanup(self.store.close)
            self.authority = AuthorityService(self.store, self.policy)
            self.budget = BudgetService(self.store)
            self.trace = TraceRuntime(self.store, self.authority)
            self.runtime = DeterministicRuntime(self.store, self.authority, self.budget, self.trace)
            if expires_after == 30:
                pass
            else:
                future = datetime.now(timezone.utc) + timedelta(minutes=1)
                with patch("kernel.authority.service._now", return_value=future):
                    with self.assertRaises(InvalidDelegation):
                        self.authority.validate_delegation_chain(grant_id)
                    with self.assertRaisesRegex(RuntimeDenied, "SCHEDULE_SETUP_COMPENSATED"):
                        self.runtime.schedule_node(**args)
            if expires_after == 30:
                with self.assertRaisesRegex(RuntimeDenied, "SCHEDULE_SETUP_COMPENSATED"):
                    self.runtime.schedule_node(**args)
            with self.store._connection() as conn:
                self.assertEqual(conn.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()[0], "CANCELLED")
                self.assertEqual(conn.execute("SELECT state FROM budget_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()[0], "RELEASED")
                result = json.loads(conn.execute("SELECT result_json FROM command_ledger WHERE command_id=?", (command_id,)).fetchone()[0])
                self.assertEqual(result["status"], "SETUP_FAILED")
                events = conn.execute("SELECT event_json FROM trace_events WHERE run_id=? AND actor_id='nexus-core-recovery'", (run_id,)).fetchall()
                self.assertEqual(len(events), 1)
                recovery_event = json.loads(events[0]["event_json"])
                self.assertEqual(recovery_event["actor_id"], "nexus-core-recovery")
                self.assertEqual(recovery_event["typed_metadata"]["recovery_intent_command_id"], command_id + "-setup-intent")
                classification = conn.execute("SELECT actor_id,reason,subject_type,subject_ref FROM classification_assertions WHERE assertion_id=?", (recovery_event["classification_assertion_ref"],)).fetchone()
                self.assertEqual(tuple(classification), ("nexus-core-recovery", "kernel recovery cleanup", "TRACE_EVENT", recovery_event["event_id"]))
            self.assertEqual(self.trace.replay_run(run_id)["status"], "CANCELLED")
            with self.assertRaisesRegex(RuntimeDenied, "SCHEDULE_SETUP_COMPENSATED"):
                self.runtime.schedule_node(**args)
            with self.store._connection() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM trace_events WHERE run_id=? AND actor_id='nexus-core-recovery'", (run_id,)).fetchone()[0], 1)

        # Existing kernel recovery Trace/classification remains valid persisted
        # infrastructure history after a normal close/reopen.
        data_root = Path(self.temp.name) / "data"
        self.store.close()
        self.store = ObjectStore(data_root)
        self.addCleanup(self.store.close)
        self.trace = TraceRuntime(self.store, AuthorityService(self.store, self.policy))
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 19)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM trace_events WHERE actor_id='nexus-core-recovery'").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM classification_assertions WHERE actor_id='nexus-core-recovery' AND subject_type='TRACE_EVENT'").fetchone()[0], 2)
        for run_id in ("run-e0", "run-e1"):
            self.assertEqual(self.trace.replay_run(run_id)["status"], "CANCELLED")

    def _exercise_kernel_recovery_release_crash_window(self, release_mode):
        command_id = "cmd-recovery-release-" + release_mode
        args = self._prepare_single_schedule(command_id=command_id)
        original_release = self.budget.release

        def reject_manifest(**kwargs):
            raise RuntimeDenied("INJECTED_SETUP_REJECTION")

        def interrupt_release(**kwargs):
            if release_mode == "before":
                raise RuntimeError("injected before budget release")
            original_release(**kwargs)
            raise RuntimeError("injected after budget release")

        with patch.object(self.runtime, "bind_manifest", side_effect=reject_manifest), \
             patch.object(self.budget, "release", side_effect=interrupt_release):
            with self.assertRaisesRegex(RuntimeError, "SCHEDULE_SETUP_COMPENSATION_FAILED"):
                self._schedule(*args, command_id=command_id)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM runs WHERE run_id='run-e0'").fetchone()[0], "CANCELLED")
            reservation = conn.execute("SELECT reservation_id,state FROM budget_reservations WHERE run_id='run-e0'").fetchone()
            expected_state = "RESERVED" if release_mode == "before" else "RELEASED"
            self.assertEqual(reservation["state"], expected_state)
            self.assertIsNone(conn.execute("SELECT 1 FROM command_ledger WHERE command_id=?", (command_id,)).fetchone())
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM trace_events WHERE run_id='run-e0' AND actor_id='nexus-core-recovery'").fetchone()[0], 1)

        data_root = Path(self.temp.name) / "data"
        self.store.close()
        self.store = ObjectStore(data_root)
        self.addCleanup(self.store.close)
        self.authority = AuthorityService(self.store, self.policy)
        self.budget = BudgetService(self.store)
        self.trace = TraceRuntime(self.store, self.authority)
        self.runtime = DeterministicRuntime(self.store, self.authority, self.budget, self.trace)
        with self.assertRaisesRegex(RuntimeDenied, "SCHEDULE_SETUP_COMPENSATED"):
            self._schedule(*args, command_id=command_id)
        self.assertEqual(self.trace.replay_run("run-e0")["status"], "CANCELLED")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT state FROM budget_reservations WHERE reservation_id=?", (reservation["reservation_id"],)).fetchone()[0], "RELEASED")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM budget_ledger WHERE reservation_id=? AND action='RELEASED'", (reservation["reservation_id"],)).fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM trace_events WHERE run_id='run-e0' AND actor_id='nexus-core-recovery'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM classification_assertions WHERE actor_id='nexus-core-recovery' AND subject_type='TRACE_EVENT' AND subject_ref='evt-" + command_id + "-setup-core-cancel'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM budget_reservations WHERE run_id='run-e0'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM runs WHERE run_id='run-e0'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM route_decisions WHERE subtask_id='node-e0'").fetchone()[0], 1)
            disposition = json.loads(conn.execute("SELECT result_json FROM command_ledger WHERE command_id=?", (command_id,)).fetchone()[0])
            self.assertEqual(disposition["status"], "SETUP_FAILED")

    def test_kernel_recovery_release_crash_before_budget_release_converges_after_reopen(self):
        self._exercise_kernel_recovery_release_crash_window("before")

    def test_kernel_recovery_release_crash_after_budget_release_converges_after_reopen(self):
        self._exercise_kernel_recovery_release_crash_window("after")

    def test_kernel_recovery_authority_rejects_unbound_and_mismatched_intents(self):
        _, recovery = self._leave_interrupted_compensation_intent()
        with self.assertRaisesRegex(TraceAdmissionDenied, "RECOVERY_INTENT_NOT_VALID"):
            self.trace._cancel_created_run_for_recovery(**{**recovery, "schedule_command_id": "missing-intent"})
        for changed, expected in (
            ({"schedule_request_hash": "0" * 64}, "RECOVERY_INTENT_NOT_VALID"),
            ({"task_id": "task-other"}, "RECOVERY_INTENT_BINDING_MISMATCH"),
            ({"root_run_id": "run-other"}, "RECOVERY_RUN_NOT_CANCELLABLE"),
            ({"subtask_id": "node-other"}, "RECOVERY_INTENT_BINDING_MISMATCH"),
            ({"run_id": "run-other"}, "RECOVERY_INTENT_BINDING_MISMATCH"),
            ({"reservation_id": "reservation-other"}, "RECOVERY_INTENT_BINDING_MISMATCH"),
        ):
            with self.subTest(changed=changed), self.assertRaisesRegex(TraceAdmissionDenied, expected):
                self.trace._cancel_created_run_for_recovery(**{**recovery, **changed})
        with self.assertRaises(TypeError):
            self.trace._cancel_created_run_for_recovery(**recovery, actor_id="model-agent")

        intent_id = recovery["schedule_command_id"] + "-setup-intent"
        other_reservation = self.budget.reserve(command_id="cmd-other-reservation", account_id="budget-1", task_id="task-1",
            run_id="run-other", amount=1, model_calls=1, tool_calls=0, child_runs=1)
        with self.store._connection() as conn:
            prior = conn.execute("SELECT result_json FROM command_ledger WHERE command_id=?", (intent_id,)).fetchone()[0]
            changed_result = json.loads(prior)
            changed_result["run_id"] = "tampered-run"
            conn.execute("DROP TRIGGER command_ledger_no_update")
            conn.execute("UPDATE command_ledger SET result_json=? WHERE command_id=?", (json.dumps(changed_result, sort_keys=True, separators=(",", ":")), intent_id))
            with self.assertRaisesRegex(TraceAdmissionDenied, "RECOVERY_INTENT_BINDING_MISMATCH"):
                self.trace._cancel_created_run_for_recovery(**recovery)
            conn.execute("UPDATE command_ledger SET result_json=? WHERE command_id=?", (prior, intent_id))
            changed_result = json.loads(prior)
            changed_result["reservation_id"] = other_reservation
            conn.execute("UPDATE command_ledger SET result_json=? WHERE command_id=?", (json.dumps(changed_result, sort_keys=True, separators=(",", ":")), intent_id))
            with self.assertRaisesRegex(TraceAdmissionDenied, "RECOVERY_INTENT_BINDING_MISMATCH"):
                self.trace._cancel_created_run_for_recovery(**{**recovery, "reservation_id": other_reservation})
            conn.execute("UPDATE command_ledger SET result_json=? WHERE command_id=?", (prior, intent_id))
            conn.execute("CREATE TRIGGER command_ledger_no_update BEFORE UPDATE ON command_ledger BEGIN SELECT RAISE(ABORT,'COMMAND_LEDGER_IMMUTABLE'); END")

    def test_setup_compensation_recovery_continues_after_root_cancel_and_reopen(self):
        command_id = "cmd-compensation-root-cancel"
        args, _ = self._leave_interrupted_compensation_intent(command_id)
        self._advance("run-root", "cmd-compensation-root-cancelled", "RUNNING", "CANCELLED", "agent")
        data_root = Path(self.temp.name) / "data"
        self.store.close()
        self.store = ObjectStore(data_root)
        self.addCleanup(self.store.close)
        self.authority = AuthorityService(self.store, self.policy)
        self.budget = BudgetService(self.store)
        self.trace = TraceRuntime(self.store, self.authority)
        self.runtime = DeterministicRuntime(self.store, self.authority, self.budget, self.trace)
        with self.assertRaisesRegex(RuntimeDenied, "SCHEDULE_SETUP_COMPENSATED"):
            self._schedule(*args, command_id=command_id)
        self.assertEqual(self.trace.replay_run("run-e0")["status"], "CANCELLED")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM runs WHERE run_id='run-root'").fetchone()[0], "CANCELLED")
            self.assertEqual(conn.execute("SELECT state FROM budget_reservations WHERE run_id='run-e0'").fetchone()[0], "RELEASED")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM trace_events WHERE run_id='run-e0' AND actor_id='nexus-core-recovery'").fetchone()[0], 1)

    def test_kernel_recovery_rejects_ready_and_running_children(self):
        command_id = "cmd-recovery-executing-child"
        args = self._prepare_single_schedule(command_id=command_id)
        original_insert_event = self.trace._insert_event
        original_transition = self.trace.transition_run
        original_recovery = self.trace._cancel_created_run_for_recovery
        captured = {}

        def reject_ready_transition(**kwargs):
            if kwargs["command_id"] == command_id + "-ready":
                raise TraceAdmissionDenied("INJECTED_READY_TRANSITION_REJECTION")
            return original_transition(**kwargs)

        def interrupt_recovery_trace(conn, event):
            if event["actor_id"] == "nexus-core-recovery":
                raise RuntimeError("injected recovery trace interruption")
            return original_insert_event(conn, event)

        def capture_recovery_request(**kwargs):
            captured.update(kwargs)
            return original_recovery(**kwargs)

        with patch.object(self.trace, "transition_run", side_effect=reject_ready_transition), \
             patch.object(self.trace, "_insert_event", side_effect=interrupt_recovery_trace), \
             patch.object(self.trace, "_cancel_created_run_for_recovery", side_effect=capture_recovery_request):
            with self.assertRaisesRegex(RuntimeError, "SCHEDULE_SETUP_COMPENSATION_FAILED"):
                self._schedule(*args, command_id=command_id)

        self._advance("run-e0", command_id + "-ready", "CREATED", "READY", "model-agent")
        with self.assertRaisesRegex(TraceAdmissionDenied, "RECOVERY_RUN_NOT_CANCELLABLE"):
            self.trace._cancel_created_run_for_recovery(**captured)
        self._event_class(command_id + "-running", "model-agent")
        self._advance("run-e0", command_id + "-running", "READY", "RUNNING", "model-agent")
        with self.assertRaisesRegex(TraceAdmissionDenied, "RECOVERY_RUN_NOT_CANCELLABLE"):
            self.trace._cancel_created_run_for_recovery(**captured)
        self.assertEqual(self.trace.replay_run("run-e0")["status"], "RUNNING")

    def test_permanent_manifest_and_attempt_setup_failures_cancel_without_fabricating_attempts(self):
        args = self._prepare_single_schedule()
        with patch.object(self.runtime, "bind_manifest", side_effect=TraceAdmissionDenied("INJECTED_MANIFEST_REJECTION")):
            with self.assertRaisesRegex(RuntimeDenied, "SCHEDULE_SETUP_COMPENSATED"):
                self._schedule(*args, command_id="cmd-setup-failure")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM runs WHERE run_id='run-e0'").fetchone()[0], "CANCELLED")
            self.assertEqual(conn.execute("SELECT state FROM budget_reservations WHERE run_id='run-e0'").fetchone()[0], "RELEASED")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM subtask_attempts WHERE run_id='run-e0'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT status FROM subtasks WHERE subtask_id='node-e0'").fetchone()[0], "PENDING")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM route_decisions WHERE subtask_id='node-e0'").fetchone()[0], 1)

    def test_manifest_response_loss_keeps_setup_recoverable_without_duplicate_reservation_or_run(self):
        args = self._prepare_single_schedule()
        original_bind = self.runtime.bind_manifest
        lost = False

        def lose_manifest_response_once(**kwargs):
            nonlocal lost
            if not lost:
                lost = True
                raise RuntimeError("injected lost response before manifest binding")
            return original_bind(**kwargs)

        with patch.object(self.runtime, "bind_manifest", side_effect=lose_manifest_response_once):
            with self.assertRaisesRegex(RuntimeError, "lost response"):
                self._schedule(*args, command_id="cmd-setup-failure")
            recovered = self._schedule(*args, command_id="cmd-setup-failure")
        self.assertEqual(recovered["status"], "READY")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM budget_reservations WHERE run_id='run-e0'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM runs WHERE run_id='run-e0'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM subtask_attempts WHERE run_id='run-e0'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM route_decisions WHERE subtask_id='node-e0'").fetchone()[0], 1)
        self.assertEqual(self._schedule(*args, command_id="cmd-setup-failure"), recovered)

    def test_attempt_registration_rejection_compensates_and_retains_route_fact(self):
        args = self._prepare_single_schedule()
        with patch.object(self.runtime, "_register_scheduled_attempt", side_effect=RuntimeDenied("INJECTED_ATTEMPT_REJECTION")):
            with self.assertRaisesRegex(RuntimeDenied, "SCHEDULE_SETUP_COMPENSATED"):
                self._schedule(*args, command_id="cmd-setup-failure")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM runs WHERE run_id='run-e0'").fetchone()[0], "CANCELLED")
            self.assertEqual(conn.execute("SELECT state FROM budget_reservations WHERE run_id='run-e0'").fetchone()[0], "RELEASED")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM subtask_attempts WHERE run_id='run-e0'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM route_decisions WHERE subtask_id='node-e0'").fetchone()[0], 1)

    def test_ready_rejection_cancels_registered_attempt_and_cleanup_resumes_after_reopen(self):
        args = self._prepare_single_schedule()
        original_transition = self.trace.transition_run

        def fail_ready_once(**kwargs):
            if kwargs["command_id"] == "cmd-setup-failure-ready":
                raise InvalidRunTransition("INJECTED_READY_REJECTION")
            return original_transition(**kwargs)

        with patch.object(self.trace, "transition_run", side_effect=fail_ready_once):
            with self.assertRaisesRegex(RuntimeDenied, "SCHEDULE_SETUP_COMPENSATED"):
                self._schedule(*args, command_id="cmd-setup-failure")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM runs WHERE run_id='run-e0'").fetchone()[0], "CANCELLED")
            self.assertEqual(conn.execute("SELECT outcome FROM subtask_attempts WHERE run_id='run-e0'").fetchone()[0], "CANCELLED")
            node = conn.execute("SELECT status,final_outcome FROM subtasks WHERE subtask_id='node-e0'").fetchone()
            self.assertEqual(tuple(node), ("CANCELLED", "CANCELLED"))

    def test_ready_commit_with_lost_response_is_not_compensated_or_cancelled(self):
        args = self._prepare_single_schedule()
        original_transition = self.trace.transition_run

        def commit_then_lose_response(**kwargs):
            result = original_transition(**kwargs)
            if kwargs["command_id"] == "cmd-setup-failure-ready":
                raise InvalidRunTransition("INJECTED_POST_COMMIT_RESPONSE_LOSS")
            return result

        with patch.object(self.trace, "transition_run", side_effect=commit_then_lose_response):
            result = self._schedule(*args, command_id="cmd-setup-failure")
        self.assertEqual(result["status"], "READY")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM runs WHERE run_id='run-e0'").fetchone()[0], "READY")
            self.assertEqual(conn.execute("SELECT outcome FROM subtask_attempts WHERE run_id='run-e0'").fetchone()[0], "READY")
            self.assertEqual(conn.execute("SELECT state FROM budget_reservations WHERE run_id='run-e0'").fetchone()[0], "RESERVED")
            self.assertIsNone(conn.execute("SELECT 1 FROM command_ledger WHERE command_id='cmd-setup-failure-setup-cancel'").fetchone())

    def test_setup_cleanup_failure_resumes_same_command_after_store_reopen(self):
        args = self._prepare_single_schedule()
        original_insert_event = self.trace._insert_event

        def fail_recovery_trace(conn, event):
            if event["actor_id"] == "nexus-core-recovery":
                raise RuntimeError("injected cancellation cleanup interruption")
            return original_insert_event(conn, event)

        with patch.object(self.runtime, "bind_manifest", side_effect=TraceAdmissionDenied("INJECTED_MANIFEST_REJECTION")), patch.object(self.trace, "_insert_event", side_effect=fail_recovery_trace):
            with self.assertRaisesRegex(RuntimeError, "SCHEDULE_SETUP_COMPENSATION_FAILED"):
                self._schedule(*args, command_id="cmd-setup-failure")
        with self.store._connection() as conn:
            self.assertIsNotNone(conn.execute("SELECT 1 FROM command_ledger WHERE command_id='cmd-setup-failure-setup-intent'").fetchone())
            self.assertIsNone(conn.execute("SELECT 1 FROM command_ledger WHERE command_id='cmd-setup-failure'").fetchone())
            self.assertEqual(conn.execute("SELECT status FROM runs WHERE run_id='run-e0'").fetchone()[0], "CREATED")
            self.assertEqual(conn.execute("SELECT state FROM budget_reservations WHERE run_id='run-e0'").fetchone()[0], "RESERVED")

        policy = self.authority.policy
        self.store.close()
        self.store = ObjectStore(Path(self.temp.name) / "data")
        self.addCleanup(self.store.close)
        self.authority = AuthorityService(self.store, policy)
        self.budget = BudgetService(self.store)
        self.trace = TraceRuntime(self.store, self.authority)
        self.runtime = DeterministicRuntime(self.store, self.authority, self.budget, self.trace)
        with self.assertRaisesRegex(RuntimeDenied, "SCHEDULE_SETUP_COMPENSATED"):
            self._schedule(*args, command_id="cmd-setup-failure")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM runs WHERE run_id='run-e0'").fetchone()[0], "CANCELLED")
            self.assertEqual(conn.execute("SELECT state FROM budget_reservations WHERE run_id='run-e0'").fetchone()[0], "RELEASED")
            result = json.loads(conn.execute("SELECT result_json FROM command_ledger WHERE command_id='cmd-setup-failure'").fetchone()[0])
            self.assertEqual(result["status"], "SETUP_FAILED")

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

    def test_attempt_two_setup_compensation_survives_revocation_and_reopen(self):
        """A retry attempt is owned by subtask_attempts, not the legacy first-run pointer."""
        self.runtime.create_dag(command_id="cmd-attempt2-recovery-dag", task_id="task-1", root_run_id="run-root", nodes=self._nodes())
        self._activate_root()
        self._model_profile("model-e1", "E1", 1)
        self._model_profile("model-e2", "E2", 2)
        first = self._schedule("node-e1", "run-e1", "model-agent", "grant-attempt2-first", "route-attempt2-first",
            command_id="cmd-attempt2-first", requested_capability="E1")
        self._advance("run-e1", "cmd-attempt2-first-running", "READY", "RUNNING", "model-agent")
        self._advance("run-e1", "cmd-attempt2-first-failed", "RUNNING", "FAILED", "model-agent")

        command_id = "cmd-attempt2-recovery"
        args = {
            "command_id": command_id, "task_id": "task-1", "root_run_id": "run-root", "subtask_id": "node-e1",
            "child_run_id": "run-e2", "child_grant_id": "grant-attempt2-retry",
            "child_classification_assertion_ref": "class-run-e2",
            "event_classification_assertion_ref": "class-evt-" + command_id + "-create-run",
            "ready_event_classification_assertion_ref": "class-evt-" + command_id + "-ready",
            "cancelled_event_classification_assertion_ref": "class-evt-" + command_id + "-setup-cancel",
            "route_object_id": "route-e2", "route_classification_assertion_ref": "class-route-e2",
            "manifest_object_id": "manifest-run-e2", "manifest_classification_assertion_ref": "class-manifest-run-e2",
            "requested_capability": "E2", "attempt_reason": "VERIFIER_REQUIRED_ESCALATION",
            "predecessor_attempt_id": first["attempt_id"],
        }
        self._child_grant("run-e2", "model-agent", "grant-attempt2-retry")
        for assertion, subject_type, subject_ref, actor in (
            ("class-run-e2", "RUN", "run-e2", "model-agent"),
            (args["event_classification_assertion_ref"], "TRACE_EVENT", "evt-" + command_id + "-create-run", "model-agent"),
            (args["ready_event_classification_assertion_ref"], "TRACE_EVENT", "evt-" + command_id + "-ready", "model-agent"),
            (args["cancelled_event_classification_assertion_ref"], "TRACE_EVENT", "evt-" + command_id + "-setup-cancel", "model-agent"),
            ("class-route-e2", "OBJECT", "route-e2", "agent"),
            ("class-manifest-run-e2", "OBJECT", "manifest-run-e2", "model-agent"),
        ):
            self._classify(assertion, subject_type, subject_ref, actor)

        original_insert_event = self.trace._insert_event
        original_transition = self.trace.transition_run
        def interrupt_recovery_trace(conn, event):
            if event["actor_id"] == "nexus-core-recovery":
                raise RuntimeError("injected attempt-2 recovery interruption")
            return original_insert_event(conn, event)

        def reject_attempt2_ready(**kwargs):
            if kwargs["command_id"] == command_id + "-ready":
                raise InvalidRunTransition("INJECTED_ATTEMPT2_READY_REJECTION")
            return original_transition(**kwargs)

        with patch.object(self.trace, "transition_run", side_effect=reject_attempt2_ready), \
             patch.object(self.trace, "_insert_event", side_effect=interrupt_recovery_trace):
            with self.assertRaisesRegex(RuntimeError, "SCHEDULE_SETUP_COMPENSATION_FAILED"):
                self.runtime.schedule_node(**args)
        with self.store._connection() as conn:
            intent = conn.execute("SELECT result_json FROM command_ledger WHERE command_id=?", (command_id + "-setup-intent",)).fetchone()
            self.assertIsNotNone(intent)
            self.assertIsNone(conn.execute("SELECT 1 FROM command_ledger WHERE command_id=?", (command_id,)).fetchone())
            self.assertEqual(conn.execute("SELECT status FROM runs WHERE run_id='run-e2'").fetchone()[0], "CREATED")
            self.assertEqual(conn.execute("SELECT outcome FROM subtask_attempts WHERE run_id='run-e2'").fetchone()[0], "CREATED")
            self.assertEqual(conn.execute("SELECT scheduled_run_id,status FROM subtasks WHERE subtask_id='node-e1'").fetchone()[0:2], ("run-e1", "WAITING"))
            reservation_id = json.loads(intent["result_json"])["reservation_id"]
        self.authority.revoke_grant("grant-attempt2-retry", command_id + "-revoke")

        data_root = Path(self.temp.name) / "data"
        self.store.close()
        self.store = ObjectStore(data_root)
        self.addCleanup(self.store.close)
        self.authority = AuthorityService(self.store, self.policy)
        self.budget = BudgetService(self.store)
        self.trace = TraceRuntime(self.store, self.authority)
        self.runtime = DeterministicRuntime(self.store, self.authority, self.budget, self.trace)
        schedule_request = {key: args[key] for key in (
            "task_id", "root_run_id", "subtask_id", "child_run_id", "child_grant_id", "child_classification_assertion_ref",
            "event_classification_assertion_ref", "ready_event_classification_assertion_ref", "route_object_id",
            "route_classification_assertion_ref", "manifest_object_id", "manifest_classification_assertion_ref",
            "requested_capability", "attempt_reason", "predecessor_attempt_id")}
        schedule_request["cancelled_event_classification_assertion_ref"] = args["cancelled_event_classification_assertion_ref"]
        recovery_request = {"schedule_command_id": command_id, "schedule_request_hash": self.store._request_hash("schedule_subtask", schedule_request),
            "task_id": "task-1", "root_run_id": "run-root", "subtask_id": "node-e1", "run_id": "run-e2", "reservation_id": reservation_id}
        # Attack the persisted attempt projection in this disposable database;
        # recovery must reject both a mismatched command and a non-latest attempt.
        with self.store._connection() as conn:
            conn.execute("DROP TRIGGER subtask_attempts_identity_immutable")
            conn.execute("UPDATE subtask_attempts SET command_id='tampered-attempt-command' WHERE run_id='run-e2'")
        with self.assertRaisesRegex(TraceAdmissionDenied, "RECOVERY_ATTEMPT_PROJECTION_MISMATCH"):
            self.trace._cancel_created_run_for_recovery(**recovery_request)
        with self.store._connection() as conn:
            conn.execute("UPDATE subtask_attempts SET command_id=? WHERE run_id='run-e2'", (command_id,))
            conn.execute("UPDATE subtask_attempts SET attempt_no=3 WHERE run_id='run-e1'")
        with self.assertRaisesRegex(TraceAdmissionDenied, "RECOVERY_ATTEMPT_PROJECTION_MISMATCH"):
            self.trace._cancel_created_run_for_recovery(**recovery_request)
        with self.store._connection() as conn:
            conn.execute("UPDATE subtask_attempts SET attempt_no=1 WHERE run_id='run-e1'")
            conn.execute("""CREATE TRIGGER subtask_attempts_identity_immutable
                BEFORE UPDATE ON subtask_attempts
                WHEN NEW.attempt_id <> OLD.attempt_id OR NEW.task_id <> OLD.task_id OR NEW.subtask_id <> OLD.subtask_id
                  OR NEW.attempt_no <> OLD.attempt_no OR NEW.run_id <> OLD.run_id
                  OR NEW.route_decision_ref IS NOT OLD.route_decision_ref OR NEW.requested_capability <> OLD.requested_capability
                  OR NEW.attempt_reason <> OLD.attempt_reason OR NEW.predecessor_attempt_id IS NOT OLD.predecessor_attempt_id
                  OR NEW.command_id <> OLD.command_id OR NEW.created_at <> OLD.created_at
                  OR NOT (OLD.outcome=NEW.outcome OR (OLD.outcome='CREATED' AND NEW.outcome IN ('READY','RUNNING','FAILED','CANCELLED','POLICY_DENIED','BUDGET_DENIED'))
                       OR (OLD.outcome='READY' AND NEW.outcome IN ('RUNNING','FAILED','CANCELLED','POLICY_DENIED','BUDGET_DENIED'))
                       OR (OLD.outcome='RUNNING' AND NEW.outcome IN ('SUCCEEDED','FAILED','CANCELLED','INCONCLUSIVE')))
                BEGIN SELECT RAISE(ABORT, 'INVALID_SUBTASK_ATTEMPT_TRANSITION'); END""")
        with self.assertRaisesRegex(RuntimeDenied, "SCHEDULE_SETUP_COMPENSATED"):
            self.runtime.schedule_node(**args)
        self.assertEqual(self.trace.replay_run("run-e2")["status"], "CANCELLED")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT outcome FROM subtask_attempts WHERE run_id='run-e2'").fetchone()[0], "CANCELLED")
            node = conn.execute("SELECT status,final_outcome FROM subtasks WHERE subtask_id='node-e1'").fetchone()
            self.assertEqual(tuple(node), ("CANCELLED", "CANCELLED"))
            self.assertEqual(conn.execute("SELECT state FROM budget_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()[0], "RELEASED")
            self.assertEqual(json.loads(conn.execute("SELECT result_json FROM command_ledger WHERE command_id=?", (command_id,)).fetchone()[0])["status"], "SETUP_FAILED")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM trace_events WHERE run_id='run-e2' AND actor_id='nexus-core-recovery'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM budget_ledger WHERE reservation_id=? AND action='RELEASED'", (reservation_id,)).fetchone()[0], 1)
        self.assertEqual(self.trace.replay_run("run-e2")["status"], "CANCELLED")

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

    def test_effect_descriptor_exact_replay_after_grant_revocation(self):
        effects = DeterministicEffectService(self.store, self.authority, self.trace, self.budget)
        descriptor = {"schema_id":"nexus.tool_descriptor", "schema_version":1, "tool_id":"tool-read", "version":"replay-test", "input_schema_id":"nexus.object@1.schema.json", "output_schema_id":"nexus.object@1.schema.json", "effect_class":"EXTERNAL_REVERSIBLE", "required_authority":["FAKE_WRITE"], "required_classifications":["PUBLIC"], "idempotency_support":True, "reconciliation_capability":"fake-authoritative", "compensation_capability":"fake-compensate", "network_egress":False, "risk_tags":["synthetic"], "review_status":"APPROVED"}
        request = {"command_id":"cmd-descriptor-replay", "grant_id":"grant-root", "task_id":"task-1", "descriptor":descriptor}
        effects.register_descriptor(**request)
        self.authority.revoke_grant("grant-root", "cmd-descriptor-replay-revoke")
        with self.store._connection() as conn:
            before = (conn.execute("SELECT COUNT(*) FROM tool_descriptors WHERE tool_id='tool-read' AND version='replay-test'").fetchone()[0],
                      conn.execute("SELECT COUNT(*) FROM command_ledger WHERE command_id='cmd-descriptor-replay'").fetchone()[0],
                      conn.execute("SELECT COUNT(*) FROM authority_events").fetchone()[0])
        self.assertIsNone(effects.register_descriptor(**request))
        with self.assertRaises(CommandConflict):
            effects.register_descriptor(**{**request, "descriptor":{**descriptor, "risk_tags":["changed"]}})
        with self.store._connection() as conn:
            after_replay = (conn.execute("SELECT COUNT(*) FROM tool_descriptors WHERE tool_id='tool-read' AND version='replay-test'").fetchone()[0],
                            conn.execute("SELECT COUNT(*) FROM command_ledger WHERE command_id='cmd-descriptor-replay'").fetchone()[0],
                            conn.execute("SELECT COUNT(*) FROM authority_events").fetchone()[0])
            self.assertEqual(json.loads(conn.execute("SELECT result_json FROM command_ledger WHERE command_id='cmd-descriptor-replay'").fetchone()[0]), {"tool_id":"tool-read", "version":"replay-test"})
        self.assertEqual(after_replay, before)
        with self.assertRaises(AuthorizationDenied):
            effects.register_descriptor(**{**request, "command_id":"cmd-descriptor-new-after-revoke", "descriptor":{**descriptor, "version":"fresh"}})
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tool_descriptors WHERE tool_id='tool-read'").fetchone()[0], 1)

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
        authorize_class = self._event_class("cmd-denied-effect-authorize", "tool-agent")
        authorize_result = effects.authorize(command_id="cmd-denied-effect-authorize", effect_id="effect-denied", classification_assertion_ref=authorize_class)
        self._schedule("node-tool-comp", "run-tool-comp", "tool-agent", "grant-tool-comp")
        self._advance("run-tool-comp", "cmd-denied-effect-fresh-running", "READY", "RUNNING", "tool-agent")
        fresh_effect = {**effect, "effect_id":"effect-denied-fresh", "run_id":"run-tool-comp", "grant_id":"grant-tool-comp", "idempotency_key":"stable-effect-denied-fresh"}
        fresh_effect.pop("approval_ref")
        effects.create_effect(command_id="cmd-create-denied-effect-fresh", effect=fresh_effect, payload_object_ref="input-1", classification_assertion_ref=self._event_class("cmd-create-denied-effect-fresh", "tool-agent"))
        effects.prepare(command_id="cmd-denied-effect-fresh-prepare", effect_id="effect-denied-fresh", classification_assertion_ref=self._event_class("cmd-denied-effect-fresh-prepare", "tool-agent"))
        with self.assertRaisesRegex(ApprovalDenied, "APPROVAL_PAYLOAD_HASH_MISMATCH"):
            effects.commit(command_id="cmd-denied-effect-commit", effect_id="effect-denied", classification_assertion_ref=self._event_class("cmd-denied-effect-commit-outcome", "tool-agent"), start_classification_assertion_ref=self._event_class("cmd-denied-effect-commit-start", "tool-agent"))
        self.assertEqual(effects._get("effect-denied")["execution_state"], "AUTHORIZED")
        self.authority.revoke_grant("grant-root", "cmd-deny-effect-revoke-parent")
        with self.store._connection() as conn:
            trace_count_before_replay = conn.execute("SELECT COUNT(*) FROM trace_events").fetchone()[0]
            effect_before_replay = tuple(conn.execute("SELECT execution_state,effect_json,updated_at FROM effects WHERE effect_id='effect-denied'").fetchone())
            authorized_trace_count_before_replay = sum(
                json.loads(row[0])["typed_metadata"].get("effect_id") == "effect-denied"
                for row in conn.execute("SELECT event_json FROM trace_events WHERE event_type='nexus.effect.authorized'")
            )
        self.assertEqual(authorized_trace_count_before_replay, 1)
        self.assertEqual(effects.authorize(command_id="cmd-denied-effect-authorize", effect_id="effect-denied", classification_assertion_ref=authorize_class), authorize_result)
        with self.assertRaises(CommandConflict):
            effects.authorize(command_id="cmd-denied-effect-authorize", effect_id="effect-denied", classification_assertion_ref="different-classification-for-same-command")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM trace_events").fetchone()[0], trace_count_before_replay)
            self.assertEqual(tuple(conn.execute("SELECT execution_state,effect_json,updated_at FROM effects WHERE effect_id='effect-denied'").fetchone()), effect_before_replay)
            self.assertEqual(sum(
                json.loads(row[0])["typed_metadata"].get("effect_id") == "effect-denied"
                for row in conn.execute("SELECT event_json FROM trace_events WHERE event_type='nexus.effect.authorized'")
            ), 1)
        with self.assertRaises(AuthorizationDenied):
            effects.authorize(command_id="cmd-denied-effect-fresh-after-revoke", effect_id="effect-denied-fresh", classification_assertion_ref=self._event_class("cmd-denied-effect-fresh-after-revoke", "tool-agent"))
        with self.assertRaises(InvalidDelegation):
            effects.commit(command_id="cmd-denied-effect-after-revoke", effect_id="effect-denied", classification_assertion_ref=self._event_class("cmd-denied-effect-after-revoke-outcome", "tool-agent"), start_classification_assertion_ref=self._event_class("cmd-denied-effect-after-revoke-start", "tool-agent"))
        self.assertEqual(effects.dispatchers["tool-read"].calls, 0)
        self.assertEqual(effects._get("effect-denied")["execution_state"], "AUTHORIZED")

    def _root(self):
        with self.store._connection() as conn:
            return conn.execute("SELECT * FROM runs WHERE run_id='run-root'").fetchone()


if __name__ == "__main__":
    unittest.main()
