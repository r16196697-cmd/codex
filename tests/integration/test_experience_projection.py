from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter_ns
from unittest import mock

from adapters.client.__main__ import main
from adapters.client.hosted import CodexHostedBridge
from adapters.storage import ObjectStore
from kernel.authority import AuthorityService
from kernel.budget import BudgetService
from kernel.experience import ExperienceProjectionError, ExperienceProjectionService, LocalOperatorReadContext
from kernel.metering.service import MeteringService, metric, unavailable_metrics
from kernel.experience.service import _canonical_json
from kernel.memory.service import MemoryService
from kernel.participation import ParticipationModeService
from kernel.run import TraceRuntime
from kernel.runtime import DeterministicRuntime
from kernel.skills.service import SkillRegistryService
from kernel.verification import VerificationService
from tests.support.test_store import open_test_store


class ExperienceProjectionTests(unittest.TestCase):
    def setUp(self):
        self._setup_fixture()

    def _setup_fixture(self, budget_unit="test-units", with_subtask=False):
        self.temp = tempfile.TemporaryDirectory(prefix="nexus-experience-projection-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data_root = self.root / "data"
        self.policy_path = self.root / ".data.test-policy.json"
        self.journal_path = self.root / "purge-journal.jsonl"
        repository = Path(__file__).resolve().parents[2]
        self.policy = json.loads((repository / "policies" / "default-policy.json").read_text(encoding="utf-8"))
        self.policy["trust_anchors"] = ["human-root"]
        self.writer = open_test_store(
            self.data_root, policy=self.policy, independent_purge_journal_path=self.journal_path,
        )
        self.addCleanup(self.writer.close)
        self.authority = AuthorityService(self.writer, self.policy)
        self.authority.register_principal({
            "schema_id": "nexus.principal", "schema_version": 1,
            "principal_id": "human-root", "principal_type": "HUMAN", "status": "ACTIVE",
        }, "experience-human")
        self.authority.register_principal({
            "schema_id": "nexus.principal", "schema_version": 1,
            "principal_id": "experience-host", "principal_type": "SERVICE", "status": "ACTIVE",
        }, "experience-host-principal")
        self.authority.register_trust_anchor({
            "schema_id": "nexus.trust_anchor", "schema_version": 1,
            "anchor_id": "experience-anchor", "principal_id": "human-root", "policy_ref": "1",
        }, "experience-anchor-register")
        self.task_id = "experience-task"
        self.root_run_id = "experience-root-run"
        self.grant_id = "experience-root-grant"
        self.input_id = "experience-input"
        self.contract_id = "experience-contract"
        self.manifest_id = "experience-root-manifest"
        self.command_id = "experience-root-create"
        self.created_at = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        event_ids = [
            f"evt-{self.command_id}-root-create", f"evt-{self.command_id}-trace-input",
            f"evt-{self.command_id}-root-ready", f"evt-{self.command_id}-root-running",
            "evt-experience-finish-verifying", "evt-experience-finish-succeeded", "evt-experience-finish-failed",
        ]
        resources = [
            f"task:{self.task_id}", "runtime-mode:instance", self.root_run_id,
            self.input_id, self.contract_id, self.manifest_id, *event_ids,
            "skill-instruction-artifact", "experience-skill-artifact-class",
            "verify-experience-quarantined", "verify-experience-admitted",
            "candidate-experience-quarantined", "candidate-experience-admitted",
            "experience-child-run",
        ]
        now = datetime.now(timezone.utc)
        self.authority.create_grant({
            "schema_id": "nexus.delegation_grant", "schema_version": 1, "grant_id": self.grant_id,
            "issued_by": "human-root", "granted_to": "experience-host", "task_scope": [self.task_id],
            "resource_scope": resources,
            "action_scope": ["RUN_CREATE", "RUN_TRANSITION", "TRACE_APPEND", "OBJECT_WRITE", "CLASSIFY",
                             "VERIFY", "INSPECT", "MEMORY_ADMIT", "MEMORY_SEARCH", "TOOL_READ"],
            "audience_scope": ["nexus-runtime", "nexus-inspect"], "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(days=2)).isoformat(), "status": "ACTIVE", "policy_version": "1",
        }, "experience-grant-create")
        self.boundary = {"allowed_classifications": ["PUBLIC"], "handling_tags": []}
        self.contract = {
            "schema_id": "nexus.task_contract", "schema_version": 1, "goal": "synthetic read projection fixture",
            "constraints": [], "success_criteria": ["exercise bounded deterministic projection"],
            "risk_class": "LOW", "routing_constraints": {"allowed_providers": [], "forbidden_providers": [],
                "locality": "LOCAL_ONLY", "network_required": False, "modalities": ["text"]},
            "routing_preferences": {"optimize_for": "BALANCED"}, "created_at": self.created_at,
        }
        self.bridge = CodexHostedBridge(
            store=self.writer, authority=self.authority, budget=BudgetService(self.writer),
            trace=TraceRuntime(self.writer, self.authority),
            runtime=DeterministicRuntime(self.writer, self.authority, BudgetService(self.writer), TraceRuntime(self.writer, self.authority)),
            verifier=VerificationService(self.writer, self.authority),
        )
        self.bridge.create_task_root(
            command_id=self.command_id, created_at=self.created_at, task_id=self.task_id,
            requester_id="human-root", grant_id=self.grant_id, root_run_id=self.root_run_id,
            budget_account_id="experience-budget",
            budget_limits={"amount_limit": 10, "unit": budget_unit, "model_call_limit": 2,
                           "tool_call_limit": 2, "child_run_limit": 2},
            input_object_id=self.input_id,
            input_payload=b"synthetic input that must never appear in the projection",
            task_contract=self.contract, contract_object_id=self.contract_id,
            dag_nodes=[self._subtask_node()] if with_subtask else [],
            root_manifest_object_id=self.manifest_id, data_boundary=self.boundary,
            classifications=self._root_classes(),
        )

    def _subtask_node(self):
        return {"schema_id": "nexus.subtask", "schema_version": 1,
                "subtask_id": "experience-subtask", "task_id": self.task_id,
                "input_object_refs": [self.input_id], "input_schema_id": "nexus.object@1.schema.json",
                "output_schema_id": "nexus.object@1.schema.json", "dependency_ids": [],
                "quality_requirement": "ROUTINE", "risk_class": "LOW", "validation_method": "SCHEMA",
                "budget_amount": 1, "requested_executor": "TOOL", "tool_id": "fixture-tool",
                "required_modalities": ["text"], "created_at": self.created_at}

    def test_project_task_pending_subtask(self):
        self.writer.close()
        self._setup_fixture(with_subtask=True)
        with self._reader() as reader:
            result = self._project(reader)
            self.assertEqual(result["subtasks_and_attempts"]["subtasks"]["items"][0]["status"], "PENDING")

    def test_project_task_stale_subtask(self):
        self.writer.close()
        self._setup_fixture(with_subtask=True)
        with self.writer._connection() as conn:
            conn.execute("UPDATE subtasks SET status='STALE' WHERE subtask_id='experience-subtask'")
        with self._reader() as reader:
            result = self._project(reader)
            self.assertEqual(result["subtasks_and_attempts"]["subtasks"]["items"][0]["status"], "STALE")

    def test_project_task_terminal_subtask_with_final_outcome(self):
        self.writer.close()
        self._setup_fixture(with_subtask=True)
        self.authority.record_classification_assertion(
            self._classification("experience-child-class", "RUN", "experience-child-run"),
            grant_id=self.grant_id, task_id=self.task_id, audience="nexus-runtime",
            command_id="experience-child-classify")
        # Disposable SQL fixture represents a canonical terminal child/Attempt;
        # the projection still reads real tables and validates its complete result.
        with self.writer._connection() as conn:
            conn.execute("INSERT INTO runs(run_id,task_id,subtask_id,parent_run_id,executor_kind,status,"
                         "grant_id,data_boundary_json,classification_assertion_ref,created_at) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?)",
                         ("experience-child-run", self.task_id, "experience-subtask", self.root_run_id,
                          "TOOL", "SUCCEEDED", self.grant_id, json.dumps(self.boundary),
                          "experience-child-class", self.created_at))
            conn.execute("INSERT INTO subtask_attempts(attempt_id,task_id,subtask_id,attempt_no,run_id,"
                         "requested_capability,attempt_reason,outcome,command_id,created_at) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?)",
                         ("experience-attempt", self.task_id, "experience-subtask", 1, "experience-child-run",
                          "TOOL", "INITIAL", "SUCCEEDED", "experience-attempt-bind", self.created_at))
            for status in ("READY", "RUNNING"):
                conn.execute("UPDATE subtasks SET status=? WHERE subtask_id='experience-subtask'", (status,))
            conn.execute("UPDATE subtasks SET status='SUCCEEDED',final_attempt_id='experience-attempt',"
                         "final_outcome='SUCCEEDED',finalized_at=? WHERE subtask_id='experience-subtask'",
                         (self.created_at,))
        with self._reader() as reader:
            result = self._project(reader)
            item = result["subtasks_and_attempts"]["subtasks"]["items"][0]
            self.assertEqual(item["status"], "SUCCEEDED")
            self.assertEqual(item["final_outcome"], "SUCCEEDED")
            self.assertEqual(item["final_attempt_id"], "experience-attempt")

    def test_project_task_budget_unit_never_echoes_untrusted_text(self):
        for unit in (r"C:\Users\fixture\private.txt", "/home/fixture/private.txt",
                     "Bearer fixture-secret", "api_key=fixture-secret",
                     "https://example.invalid/?access_token=fixture-secret", "USD", "tokens"):
            with self.subTest(unit=unit):
                self.writer.close()
                self._setup_fixture(budget_unit=unit)
                with self._reader() as reader:
                    result = self._project(reader)
                    limits = result["budget"]["limits"]
                    if unit in {"USD", "tokens"}:
                        self.assertEqual(limits["unit"], unit)
                        self.assertEqual(limits["unit_status"], "KNOWN_SAFE")
                    else:
                        self.assertEqual(limits["unit"], "OTHER")
                        self.assertEqual(limits["unit_status"], "REDACTED")
                        self.assertNotIn(unit, json.dumps(result))

    def test_mirrored_projection_enums_match_canonical_contracts(self):
        repository = Path(__file__).resolve().parents[2]
        definitions = json.loads((repository / "schemas/nexus.experience_projection@1.schema.json").read_text())["$defs"]
        for projection, canonical, fields in (
            ("run", "nexus.run@1.schema.json", ("executor_kind", "status")),
            ("routeV2", "nexus.route_decision@2.schema.json",
             ("requested_capability", "actual_executor_kind", "execution_source", "model_identity_status")),
            ("routeV1", "nexus.route_decision@1.schema.json",
             ("risk_class", "quality_requirement", "selected_model_class")),
            ("effectItem", "nexus.effect@1.schema.json",
             ("execution_state", "effect_outcome", "reconciliation_status")),
            ("verificationItem", "nexus.verification_result@1.schema.json", ("verdict", "verifier_kind")),
        ):
            properties = json.loads((repository / "schemas" / canonical).read_text())["properties"]
            for field in fields:
                with self.subTest(projection=projection, field=field):
                    self.assertEqual(definitions[projection]["properties"][field]["enum"], properties[field]["enum"])
        self.assertEqual(definitions["subtask"]["properties"]["status"]["enum"],
                         ["PENDING", "READY", "RUNNING", "WAITING", "SUCCEEDED", "FAILED", "CANCELLED", "STALE"])
        self.assertEqual(set(definitions["subtask"]["properties"]["final_outcome"]["enum"]),
                         {"SUCCEEDED", "FAILED", "CANCELLED", "INCONCLUSIVE", "POLICY_DENIED", "BUDGET_DENIED", None})

    def test_project_task_metering_unknown_estimation_fields_are_not_exposed(self):
        unsafe_field = "https://example.invalid/?api_key=fixture-secret"
        metrics = unavailable_metrics()
        metrics["estimated_cost"] = metric(
            0.004, "HOST_DECLARED", unit="USD", estimate_status="ESTIMATED",
            estimation_basis={"pricing_id": "fixture", "pricing_version": "1", "formula": "fixture"})
        # Metering v2 accepts broader basis objects than the current producer helper.
        metrics["estimated_cost"]["estimation_basis"][unsafe_field] = "caller-controlled value"
        metering = MeteringService(self.writer, self.authority, ParticipationModeService(self.writer))
        metering._insert_record(record_id="meter-unknown-basis-field", task_id=self.task_id,
                                run_id=self.root_run_id, source="HOST_DECLARED", mode="ACTIVE",
                                context_pack_ref=None, metrics=metrics)
        with self._reader() as reader:
            result = self._project(reader)
            cost = result["metering"]["items"][0]["metrics"]["estimated_cost"]
            self.assertEqual(cost["estimation_basis"], {"present": True})
            self.assertEqual(cost["value"], 0.004)
            self.assertNotIn(unsafe_field, json.dumps(result))
            self.assertNotIn("caller-controlled value", json.dumps(result))

    def _classification(self, assertion_id, subject_type, subject_ref):
        return {"schema_id": "nexus.classification_assertion", "schema_version": 1,
                "assertion_id": assertion_id, "subject_type": subject_type, "subject_ref": subject_ref,
                "sensitivity_level": "PUBLIC", "handling_tags": [], "policy_version": "1",
                "reason": "synthetic integration fixture", "actor_id": "experience-host"}

    def _root_classes(self):
        return {
            "root_run": self._classification("experience-class-root", "RUN", self.root_run_id),
            "root_created_event": self._classification("experience-class-created", "TRACE_EVENT", f"evt-{self.command_id}-root-create"),
            "input_object": self._classification("experience-class-input", "OBJECT", self.input_id),
            "input_event": self._classification("experience-class-input-event", "TRACE_EVENT", f"evt-{self.command_id}-trace-input"),
            "task_contract": self._classification("experience-class-contract", "OBJECT", self.contract_id),
            "root_manifest": self._classification("experience-class-manifest", "OBJECT", self.manifest_id),
            "root_ready_event": self._classification("experience-class-ready", "TRACE_EVENT", f"evt-{self.command_id}-root-ready"),
            "root_running_event": self._classification("experience-class-running", "TRACE_EVENT", f"evt-{self.command_id}-root-running"),
        }

    def _transition(self, terminal_state):
        trace = TraceRuntime(self.writer, self.authority)
        for target, assertion_id, event_suffix, expected in (
            ("VERIFYING", "experience-class-verifying", "verifying", "RUNNING"),
            (terminal_state, "experience-class-terminal", "succeeded" if terminal_state == "SUCCEEDED" else "failed", "VERIFYING"),
        ):
            event_ref = f"evt-experience-finish-{event_suffix}"
            self.authority.record_classification_assertion(
                self._classification(assertion_id, "TRACE_EVENT", event_ref), grant_id=self.grant_id,
                task_id=self.task_id, audience="nexus-runtime", command_id=f"experience-classify-{event_suffix}",
            )
            trace.transition_run(
                command_id=f"experience-finish-{event_suffix}", run_id=self.root_run_id,
                expected_state=expected, next_state=target, classification_assertion_ref=assertion_id,
            )

    def _snapshot_counts(self, store=None):
        with (store or self.writer)._connection() as conn:
            tables = ("tasks", "runs", "subtasks", "subtask_attempts", "trace_events", "verification_results",
                      "effects", "approval_decisions", "value_metering_records", "context_pack_records",
                      "memory_candidates", "memory_candidate_evidence", "admitted_memory_rows",
                      "skill_resolution_records", "logical_refs", "object_relations", "object_envelopes",
                      "object_states", "classification_assertions", "command_ledger")
            return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}

    def _db_commitment(self):
        database = self.data_root / "nexus.sqlite"
        stat = database.stat()
        return hashlib.sha256(database.read_bytes()).hexdigest(), stat.st_size, stat.st_mtime_ns

    def _reader(self):
        self.writer.close()
        return ObjectStore(self.data_root, policy=self.policy,
                           independent_purge_journal_path=self.journal_path, read_only=True)

    def _project(self, reader):
        return ExperienceProjectionService(reader, read_context=LocalOperatorReadContext()).project_task(self.task_id)

    def test_task_succeeded_without_semantic_verification_is_lifecycle_only(self):
        self._transition("SUCCEEDED")
        reader = self._reader()
        self.addCleanup(reader.close)
        before = self._snapshot_counts(reader)
        service = ExperienceProjectionService(reader, read_context=LocalOperatorReadContext())
        first = service.project_task(self.task_id)
        second = service.project_task(self.task_id)
        self.assertEqual(first, second)
        self.assertEqual(_canonical_json(first), _canonical_json(second))
        self.assertEqual(first["lifecycle"]["task_status"], "SUCCEEDED")
        self.assertEqual(first["lifecycle"]["root_run_status"], "SUCCEEDED")
        self.assertEqual(first["lifecycle"]["semantic_quality"], "UNKNOWN")
        self.assertEqual(first["verification"]["whole_task_verification"], "NONE")
        self.assertEqual(first["unknowns"]["whole_task_verification"], "NONE")
        self.assertEqual(first["continuation"]["status"], "NONE_RECORDED")
        self.assertEqual(first["continuation"]["lifecycle_requirement"], "OPTIONAL_INDEPENDENT_SURFACE")
        self.assertEqual(first["governance_coverage"]["status"], "NEXUS_GOVERNED")
        self.assertEqual(first["human_signals"]["interactive_tty_confirmation"], "UNKNOWN")
        self.assertEqual(before, self._snapshot_counts(reader))
        self.assertNotIn("synthetic input that must never appear", json.dumps(first))

    def test_task_failed_is_not_collapsed_into_semantic_quality(self):
        self._transition("FAILED")
        reader = self._reader()
        self.addCleanup(reader.close)
        result = self._project(reader)
        self.assertEqual(result["lifecycle"]["task_status"], "FAILED")
        self.assertEqual(result["lifecycle"]["root_run_status"], "FAILED")
        self.assertEqual(result["lifecycle"]["semantic_quality"], "UNKNOWN")
        self.assertIn("TASK_LIFECYCLE", {item["source_kind"] for item in result["failure_signals"]["items"]})

    def test_unknown_task_and_writable_store_fail_closed(self):
        with self.assertRaises(ExperienceProjectionError) as missing:
            ExperienceProjectionService(self.writer).project_task("absent-task")
        self.assertEqual(missing.exception.reason_code, "EXPERIENCE_READ_ONLY_STORE_REQUIRED")
        reader = self._reader()
        self.addCleanup(reader.close)
        with self.assertRaises(ExperienceProjectionError) as missing:
            ExperienceProjectionService(reader, read_context=LocalOperatorReadContext()).project_task("absent-task")
        self.assertEqual(missing.exception.reason_code, "EXPERIENCE_TASK_NOT_FOUND")

    def test_projection_requires_an_explicit_authorized_reader_context(self):
        reader = self._reader()
        self.addCleanup(reader.close)
        with self.assertRaises(ExperienceProjectionError) as missing:
            ExperienceProjectionService(reader).project_task(self.task_id)
        self.assertEqual(missing.exception.reason_code, "EXPERIENCE_READER_AUTHORIZATION_REQUIRED")

        class DeniedReader:
            def authorize_task_read(self, _task_id):
                return False

        with self.assertRaises(ExperienceProjectionError) as denied:
            ExperienceProjectionService(reader, read_context=DeniedReader()).project_task(self.task_id)
        self.assertEqual(denied.exception.reason_code, "EXPERIENCE_READER_AUTHORIZATION_DENIED")
        self.assertFalse(LocalOperatorReadContext(caller_kind="REMOTE").authorize_task_read(self.task_id))

        # The local CLI trust model is explicit and distinct from the Task's
        # execution Grant. This context is not a remote/MCP authorization.
        result = ExperienceProjectionService(reader, read_context=LocalOperatorReadContext()).project_task(self.task_id)
        self.assertEqual(result["identity"]["task_id"], self.task_id)

    def test_experience_projection_schema_rejects_missing_critical_nested_fields(self):
        reader = self._reader()
        self.addCleanup(reader.close)
        projection = self._project(reader)
        reader._validate("nexus.experience_projection@1.schema.json", projection)
        from jsonschema import ValidationError
        cases = (
            ("identity", "task_id"), ("lifecycle", "task_status"),
            ("verification", "whole_task_verification"), ("effects", "counts_by_outcome"),
            ("metering", "metric_interpretation"), ("context", "model_consumption"),
            ("skill_resolution", "causal_effectiveness"),
            ("memory_participation", "mutated"), ("continuation", "provenance_scope"),
            ("governance_coverage", "security_visibility"),
            ("human_signals", "interactive_tty_confirmation"), ("unknowns", "root_cause"),
        )
        for section, field in cases:
            malformed = copy.deepcopy(projection)
            del malformed[section][field]
            with self.subTest(section=section, field=field):
                with self.assertRaises(ValidationError):
                    reader._validate("nexus.experience_projection@1.schema.json", malformed)

        oversized = copy.deepcopy(projection)
        oversized["failure_signals"]["items"] = [{}] * 101
        with self.assertRaises(ValidationError):
            reader._validate("nexus.experience_projection@1.schema.json", oversized)

    def test_attempt_projection_preserves_order_predecessor_and_no_causal_claim(self):
        service = ExperienceProjectionService(self.writer)
        subtask_id = "node-retry"
        node = {"schema_id": "nexus.subtask", "schema_version": 1, "subtask_id": subtask_id,
                "task_id": self.task_id, "input_object_refs": [self.input_id],
                "input_schema_id": "nexus.object@1.schema.json", "output_schema_id": "nexus.object@1.schema.json",
                "dependency_ids": [], "quality_requirement": "ROUTINE", "risk_class": "LOW",
                "validation_method": "SCHEMA", "budget_amount": 1, "requested_executor": "TOOL",
                "tool_id": "fixture-tool", "required_modalities": ["text"], "created_at": self.created_at}
        attempts = [
            {"attempt_id": "attempt-1", "task_id": self.task_id, "subtask_id": subtask_id,
             "attempt_no": 1, "run_id": "attempt-run-1", "route_decision_ref": None,
             "requested_capability": "TOOL", "attempt_reason": "INITIAL", "predecessor_attempt_id": None,
             "outcome": "FAILED", "created_at": self.created_at},
            {"attempt_id": "attempt-2", "task_id": self.task_id, "subtask_id": subtask_id,
             "attempt_no": 2, "run_id": "attempt-run-2", "route_decision_ref": None,
             "requested_capability": "TOOL", "attempt_reason": "RETRY", "predecessor_attempt_id": "attempt-1",
             "outcome": "FAILED", "created_at": self.created_at},
            {"attempt_id": "attempt-3", "task_id": self.task_id, "subtask_id": subtask_id,
             "attempt_no": 3, "run_id": "attempt-run-3", "route_decision_ref": None,
             "requested_capability": "TOOL", "attempt_reason": "RETRY", "predecessor_attempt_id": "attempt-2",
             "outcome": "SUCCEEDED", "created_at": self.created_at},
        ]
        subtasks = [{"subtask_id": subtask_id, "task_id": self.task_id, "node_index": 0,
                     "node_json": json.dumps(node), "status": "SUCCEEDED", "scheduled_run_id": None,
                     "final_attempt_id": "attempt-3", "final_outcome": "SUCCEEDED",
                     "finalized_at": self.created_at, "created_at": self.created_at}]
        projected, attempt_projection, _, failures = service._subtasks(
            subtasks, attempts, [], {f"attempt-run-{n}": {"run_id": f"attempt-run-{n}",
                "task_id": self.task_id, "subtask_id": subtask_id, "parent_run_id": self.root_run_id}
                for n in range(1, 4)}, {"classifications": {"PUBLIC"}, "tags": set()},
        )
        self.assertEqual([item["attempt_no"] for item in attempt_projection], [1, 2, 3])
        self.assertEqual([item["predecessor_attempt_id"] for item in attempt_projection], [None, "attempt-1", "attempt-2"])
        self.assertEqual(projected[0]["final_attempt_id"], "attempt-3")
        self.assertEqual(projected[0]["final_outcome"], "SUCCEEDED")
        self.assertEqual(len(failures), 2)
        self.assertFalse(any("because" in str(item).lower() for item in failures))

    def test_policy_and_budget_denied_attempts_remain_distinct_failure_signals(self):
        service = ExperienceProjectionService(self.writer)
        nodes, attempts, run_records = [], [], {}
        for index, outcome in enumerate(("POLICY_DENIED", "BUDGET_DENIED"), start=1):
            subtask_id = f"node-denied-{index}"
            attempt_id = f"attempt-denied-{index}"
            run_id = f"attempt-denied-run-{index}"
            node = {"schema_id": "nexus.subtask", "schema_version": 1, "subtask_id": subtask_id,
                    "task_id": self.task_id, "input_object_refs": [self.input_id],
                    "input_schema_id": "nexus.object@1.schema.json", "output_schema_id": "nexus.object@1.schema.json",
                    "dependency_ids": [], "quality_requirement": "ROUTINE", "risk_class": "LOW",
                    "validation_method": "SCHEMA", "budget_amount": 1, "requested_executor": "TOOL",
                    "tool_id": "fixture-tool", "required_modalities": ["text"], "created_at": self.created_at}
            nodes.append({"subtask_id": subtask_id, "task_id": self.task_id, "node_index": index,
                          "node_json": json.dumps(node), "status": "FAILED", "scheduled_run_id": None,
                          "final_attempt_id": None, "final_outcome": None, "finalized_at": None,
                          "created_at": self.created_at})
            attempts.append({"attempt_id": attempt_id, "task_id": self.task_id, "subtask_id": subtask_id,
                             "attempt_no": 1, "run_id": run_id, "route_decision_ref": None,
                             "requested_capability": "TOOL", "attempt_reason": "INITIAL",
                             "predecessor_attempt_id": None, "outcome": outcome, "created_at": self.created_at})
            run_records[run_id] = {"run_id": run_id, "task_id": self.task_id,
                                   "subtask_id": subtask_id, "parent_run_id": self.root_run_id}
        _, projected, _, failures = service._subtasks(
            nodes, attempts, [], run_records, {"classifications": {"PUBLIC"}, "tags": set()},
        )
        self.assertEqual([item["outcome"] for item in projected], ["POLICY_DENIED", "BUDGET_DENIED"])
        self.assertEqual({item["reason_code"] for item in failures}, {"POLICY_DENIED", "BUDGET_DENIED"})

    def test_malformed_attempt_chain_fails_closed(self):
        service = ExperienceProjectionService(self.writer)
        subtask_id = "node-bad-chain"
        node = {"schema_id": "nexus.subtask", "schema_version": 1, "subtask_id": subtask_id,
                "task_id": self.task_id, "input_object_refs": [self.input_id],
                "input_schema_id": "nexus.object@1.schema.json", "output_schema_id": "nexus.object@1.schema.json",
                "dependency_ids": [], "quality_requirement": "ROUTINE", "risk_class": "LOW",
                "validation_method": "SCHEMA", "budget_amount": 1, "requested_executor": "TOOL",
                "tool_id": "fixture-tool", "required_modalities": ["text"], "created_at": self.created_at}
        attempt = {"attempt_id": "attempt-bad-chain", "task_id": self.task_id, "subtask_id": subtask_id,
                   "attempt_no": 2, "run_id": "attempt-bad-chain-run", "route_decision_ref": None,
                   "requested_capability": "TOOL", "attempt_reason": "RETRY",
                   "predecessor_attempt_id": "missing-predecessor", "outcome": "FAILED", "created_at": self.created_at}
        with self.assertRaises(ExperienceProjectionError) as error:
            service._subtasks(
                [{"subtask_id": subtask_id, "task_id": self.task_id, "node_index": 0,
                  "node_json": json.dumps(node), "status": "RUNNING", "scheduled_run_id": None,
                  "final_attempt_id": None, "final_outcome": None, "finalized_at": None,
                  "created_at": self.created_at}],
                [attempt], [], {attempt["run_id"]: {"run_id": attempt["run_id"], "task_id": self.task_id,
                    "subtask_id": subtask_id, "parent_run_id": self.root_run_id}},
                {"classifications": {"PUBLIC"}, "tags": set()},
            )
        self.assertEqual(error.exception.reason_code, "EXPERIENCE_ATTEMPT_SEQUENCE_INVALID")

    def test_hidden_route_decision_does_not_leak_duplicated_database_details(self):
        service = ExperienceProjectionService(self.writer)
        subtask_id = "node-hidden-route"
        decision = {"schema_id": "nexus.route_decision", "schema_version": 2,
                    "route_decision_id": "route-hidden", "subtask_id": subtask_id, "attempt_no": 1,
                    "requested_capability": "TOOL", "actual_executor_kind": "TOOL",
                    "execution_source": "DETERMINISTIC_RUNTIME", "model_identity_status": "NOT_APPLICABLE",
                    "reason_codes": ["PRIVATE_REASON"], "created_at": self.created_at}
        route_row = {"route_decision_id": "route-hidden", "subtask_id": subtask_id,
                     "decision_object_id": "route-hidden", "decision_json": json.dumps(decision),
                     "created_at": self.created_at}
        with mock.patch.object(service, "_object_visibility", return_value="REDACTED"):
            _, _, routes, _ = service._subtasks([], [], [route_row], {},
                {"classifications": {"PUBLIC"}, "tags": set()})
        self.assertEqual(routes, [{"route_decision_id": "REDACTED", "availability": "REDACTED"}])
        self.assertNotIn("PRIVATE_REASON", json.dumps(routes))
        with mock.patch.object(service, "_object_visibility", return_value="AVAILABLE"), \
                mock.patch.object(self.writer, "get_payload", return_value=_canonical_json(decision)), \
                mock.patch.object(self.writer, "get_object_metadata", return_value={"object_type": "artifact"}):
            _, _, routes, _ = service._subtasks([], [], [route_row], {},
                {"classifications": {"PUBLIC"}, "tags": set()})
        self.assertEqual(routes[0]["requested_capability"], "TOOL")
        with mock.patch.object(service, "_object_visibility", return_value="AVAILABLE"), \
                mock.patch.object(self.writer, "get_payload", return_value=b"{}"):
            with self.assertRaises(ExperienceProjectionError) as error:
                service._subtasks([], [], [route_row], {}, {"classifications": {"PUBLIC"}, "tags": set()})
        self.assertEqual(error.exception.reason_code, "EXPERIENCE_ROUTE_OBJECT_INTEGRITY_INVALID")

        legacy = {"schema_id": "nexus.route_decision", "schema_version": 1,
                  "route_decision_id": "route-legacy", "run_or_subtask_id": subtask_id,
                  "route_policy_version": "1", "task_class": "PRIVATE_TASK_CLASS",
                  "risk_class": "LOW", "quality_requirement": "ROUTINE", "user_policy_ref": "1",
                  "eligible_models": [], "excluded_models": [], "exclusion_reasons": [],
                  "selected_model_class": "E0", "selected_model_id": "private-model-id",
                  "selected_model_profile_version": "1", "reason_codes": ["NO_REASON"],
                  "budget_snapshot": {"account_id": "budget-fixture", "unit": "tokens", "limit": 10,
                      "reserved": 0, "consumed": 0, "remaining": 10, "model_calls_remaining": 1,
                      "tool_calls_remaining": 1, "child_runs_remaining": 0},
                  "escalation_allowed": False, "created_at": self.created_at}
        legacy_row = {"route_decision_id": "route-legacy", "subtask_id": subtask_id,
                      "decision_object_id": "route-legacy", "decision_json": json.dumps(legacy),
                      "created_at": self.created_at}
        with mock.patch.object(service, "_object_visibility", return_value="AVAILABLE"), \
                mock.patch.object(self.writer, "get_payload", return_value=_canonical_json(legacy)), \
                mock.patch.object(self.writer, "get_object_metadata", return_value={"object_type": "artifact"}):
            _, _, routes, _ = service._subtasks([], [], [legacy_row], {},
                {"classifications": {"PUBLIC"}, "tags": set()})
        self.assertEqual(routes[0]["schema_version"], 1)
        self.assertNotIn("private-model-id", json.dumps(routes))

    def test_purged_context_and_memory_sources_stay_redacted_unknown(self):
        service = ExperienceProjectionService(self.writer)
        context = service._contexts([{"pack_ref": "ctx-secret", "run_id": self.root_run_id,
            "task_id": self.task_id, "content_hash": "d" * 64, "serialized_byte_size": 100,
            "state": "PURGED", "compiled_at": self.created_at}], {self.root_run_id})
        self.assertEqual(context["items"][0]["pack_ref"], "REDACTED_PURGED")
        self.assertEqual(context["items"][0]["model_visible_exposure"], "UNKNOWN")
        memory = service._memory([{"candidate_id": "candidate-secret", "verification_ref": "verify-visible",
            "status": "PURGED", "truth_state": "VERIFIED"}], [{"verification_id": "verify-visible"}],
            {"classifications": {"PUBLIC"}, "tags": set()})
        self.assertEqual(memory["items"][0]["truth_state"], "UNKNOWN")
        self.assertEqual(memory["items"][0]["candidate_id"], "REDACTED_PURGED")
        self.assertEqual(memory["items"][0]["verification_ref"], "REDACTED_PURGED")
        self.assertNotIn("candidate-secret", json.dumps(memory))

    def test_target_specific_verification_pass_does_not_prove_task(self):
        service = ExperienceProjectionService(self.writer)
        independence = {"generator_independence": "INDEPENDENT", "evidence_independence": "INDEPENDENT",
                        "method_independence": "INDEPENDENT"}
        doc = {"schema_id": "nexus.verification_result", "schema_version": 1,
               "verification_id": "verify-experience-input", "target_ref": self.input_id,
               "verdict": "PASS", "verifier_kind": "T1_DETERMINISTIC", "evidence_used": [],
               "missing_evidence": [], "conflicts": [], "independence": independence,
               "rationale_summary": "synthetic", "run_id": self.root_run_id, "policy_version": "1"}
        row = {"verification_id": doc["verification_id"], "target_ref": self.input_id, "verdict": "PASS",
               "verifier_kind": "T1_DETERMINISTIC", "evidence_used_json": "[]",
               "independence_json": json.dumps(independence, sort_keys=True), "result_json": json.dumps(doc, sort_keys=True),
               "attester_principal_id": None, "approval_ref": None, "run_id": self.root_run_id,
               "created_at": self.created_at}
        result = service._verifications([row], {self.root_run_id}, {"classifications": {"PUBLIC"}, "tags": set()})
        self.assertEqual(result["items"][0]["verdict"], "PASS")
        self.assertEqual(result["items"][0]["target_scope"], "TARGET_SPECIFIC")
        self.assertEqual(result["whole_task_verification"], "UNKNOWN")
        self.assertEqual(result["semantic_quality"], "UNKNOWN")

    def test_verification_outcomes_and_classification_redaction_stay_target_specific(self):
        service = ExperienceProjectionService(self.writer)
        independence = {"generator_independence": "UNKNOWN", "evidence_independence": "UNKNOWN",
                        "method_independence": "UNKNOWN"}
        results = []
        for suffix, verdict in (("pass", "PASS"), ("fail", "FAIL"), ("inconclusive", "INCONCLUSIVE")):
            doc = {"schema_id": "nexus.verification_result", "schema_version": 1,
                   "verification_id": f"verify-{suffix}", "target_ref": self.input_id, "verdict": verdict,
                   "verifier_kind": "T1_DETERMINISTIC", "evidence_used": [], "missing_evidence": [],
                   "conflicts": [], "independence": independence, "rationale_summary": "synthetic",
                   "run_id": self.root_run_id, "policy_version": "1"}
            results.append({"verification_id": doc["verification_id"], "target_ref": self.input_id,
                "verdict": verdict, "verifier_kind": "T1_DETERMINISTIC", "evidence_used_json": "[]",
                "independence_json": json.dumps(independence, sort_keys=True),
                "result_json": json.dumps(doc, sort_keys=True), "attester_principal_id": None,
                "approval_ref": None, "run_id": self.root_run_id, "created_at": self.created_at})
        visible = {"classifications": {"PUBLIC"}, "tags": set()}
        projected = service._verifications(results, {self.root_run_id}, visible)
        self.assertEqual([item["verdict"] for item in projected["items"]], ["PASS", "FAIL", "INCONCLUSIVE"])
        self.assertEqual(projected["whole_task_verification"], "UNKNOWN")
        hidden = service._verifications(results[:1], {self.root_run_id}, {"classifications": set(), "tags": set()})
        self.assertEqual(hidden["status"], "REDACTED")
        self.assertIsNone(hidden["total_count"])
        self.assertEqual(hidden["items"], [])

    def test_effect_outcomes_and_reconciliation_remain_distinct(self):
        service = ExperienceProjectionService(self.writer)
        examples = [
            ("effect-committed", "FINISHED", "COMMITTED", "NOT_REQUIRED"),
            ("effect-not-committed", "CANCELLED", "NOT_COMMITTED", "NOT_REQUIRED"),
            ("effect-unknown", "FINISHED", "UNKNOWN", "PENDING"),
        ]
        rows = []
        for effect_id, state, outcome, reconciliation in examples:
            doc = {"schema_id": "nexus.effect", "schema_version": 1, "effect_id": effect_id,
                   "run_id": self.root_run_id, "tool_id": "fixture-tool", "action_type": "READ",
                   "target_ref": self.input_id, "idempotency_key": effect_id, "grant_id": self.grant_id,
                   "execution_state": state, "effect_outcome": outcome,
                   "reconciliation_status": reconciliation}
            rows.append({"effect_id": effect_id, "run_id": self.root_run_id, "tool_id": "fixture-tool",
                         "action_type": "READ", "grant_id": self.grant_id, "approval_ref": None,
                         "execution_state": state, "effect_outcome": outcome,
                         "reconciliation_status": reconciliation, "effect_json": json.dumps(doc),
                         "created_at": self.created_at, "updated_at": self.created_at})
        projected, failures = service._effects(rows, {self.root_run_id})
        self.assertEqual(projected["counts_by_outcome"], {"COMMITTED": 1, "NOT_COMMITTED": 1, "UNKNOWN": 1})
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["observed_state"], "PENDING")

    def test_approval_decisions_are_not_tty_confirmation_or_verification(self):
        for approval_id, decision in (("approval-exp-approve", "APPROVE"), ("approval-exp-deny", "DENY")):
            self.authority.create_approval({
                "schema_id": "nexus.approval_decision", "schema_version": 1,
                "approval_id": approval_id, "approver_principal_id": "human-root",
                "target_type": "OBJECT", "target_ref": self.input_id, "decision": decision,
                "approved_scope": ["VERIFY", self.input_id], "policy_version": "1", "issued_at": self.created_at,
            }, f"create-{approval_id}")
        with self.writer._connection() as conn:
            rows = [dict(row) for row in conn.execute(
                "SELECT approval_id,approver_principal_id,target_type,target_ref,effect_id,payload_integrity_hash,decision,"
                "approved_scope_json,policy_version,issued_at,expires_at,reason,request_ref "
                "FROM approval_decisions WHERE approval_id LIKE 'approval-exp-%' ORDER BY approval_id")]
        result = ExperienceProjectionService(self.writer)._approvals(
            rows, {self.root_run_id}, set(), {}, {"classifications": {"PUBLIC"}, "tags": set()},
        )
        self.assertEqual(result["decision_counts"], {"APPROVE": 1, "DENY": 1})
        self.assertEqual([item["decision"] for item in result["items"]], ["APPROVE", "DENY"])

    def test_memory_candidate_status_is_linked_only_by_verification_reference(self):
        verifier = VerificationService(self.writer, self.authority)
        verifier.verify_object_integrity(verification_id="verify-experience-quarantined",
            target_ref=self.input_id, evidence_refs=[self.input_id], run_id=self.root_run_id)
        memory = MemoryService(self.writer, self.authority, verifier)
        quarantined = memory.create_candidate(command_id="create-experience-quarantined",
            candidate_id="candidate-experience-quarantined", claim_ref=self.input_id, evidence_refs=[self.input_id],
            owner="human-root", classification_assertion_ref="experience-class-input",
            verification_ref="verify-experience-quarantined", review_trigger="integration fixture")
        self.assertEqual(quarantined["status"], "QUARANTINED")

        independence = {"generator_independence": "INDEPENDENT", "evidence_independence": "INDEPENDENT",
                        "method_independence": "INDEPENDENT"}
        admitted_verification_id = "verify-experience-admitted"
        payload_hash = verifier.human_payload_hash(verification_id=admitted_verification_id,
            target_ref=self.input_id, evidence_refs=[self.input_id], run_id=self.root_run_id, independence=independence)
        self.authority.create_approval({
            "schema_id": "nexus.approval_decision", "schema_version": 1,
            "approval_id": "approval-experience-admission", "approver_principal_id": "human-root",
            "target_type": "VERIFY", "target_ref": self.input_id, "effect_id": admitted_verification_id,
            "payload_integrity_hash": payload_hash, "decision": "APPROVE",
            "approved_scope": ["VERIFY", self.input_id], "policy_version": "1", "issued_at": self.created_at,
        }, "create-approval-experience-admission")
        verifier.record_human_verification(verification_id=admitted_verification_id,
            target_ref=self.input_id, evidence_refs=[self.input_id], run_id=self.root_run_id,
            approval_id="approval-experience-admission", attester_principal_id="human-root",
            independence=independence)
        admitted = memory.create_candidate(command_id="create-experience-admitted",
            candidate_id="candidate-experience-admitted", claim_ref=self.input_id, evidence_refs=[self.input_id],
            owner="human-root", classification_assertion_ref="experience-class-input",
            verification_ref=admitted_verification_id, review_trigger="integration fixture")
        self.assertEqual(admitted["status"], "ADMITTED")

        reader = self._reader()
        self.addCleanup(reader.close)
        result = self._project(reader)
        entries = result["memory_participation"]["items"]
        self.assertEqual({item["status"] for item in entries}, {"QUARANTINED", "ADMITTED"})
        self.assertEqual({item["admitted"] for item in entries}, {False, True})
        self.assertTrue(all(item["verification_ref"] in {"verify-experience-quarantined", "verify-experience-admitted"}
                            for item in entries))

    def test_memory_admission_object_must_match_candidate_claim(self):
        class Cursor:
            def __init__(self, one=None, many=()):
                self.one = one
                self.many = many

            def fetchone(self):
                return self.one

            def __iter__(self):
                return iter(self.many)

        class Connection:
            def execute(self, sql, _params=()):
                if "SELECT subject_type,subject_ref,sensitivity_level,handling_tags_json" in sql:
                    return Cursor({"subject_type": "OBJECT", "subject_ref": self_claim_ref,
                                   "sensitivity_level": "PUBLIC", "handling_tags_json": "[]"})
                if "SELECT evidence_object_id FROM memory_candidate_evidence" in sql:
                    return Cursor(many=[])
                if "SELECT object_id FROM admitted_memory_rows" in sql:
                    return Cursor({"object_id": "another-claim-object"})
                raise AssertionError("unexpected projection SQL")

        self_claim_ref = self.input_id
        service = ExperienceProjectionService(self.writer)
        candidate = {"candidate_id": "candidate-fixture", "claim_ref": self_claim_ref,
                     "owner": "human-root", "classification_assertion_ref": "class-fixture",
                     "verification_ref": "verify-fixture", "truth_state": "VERIFIED", "status": "ADMITTED",
                     "metadata_json": "{}", "created_at": self.created_at, "expires_at": None}
        with mock.patch.object(service.store, "_connection",
                               return_value=contextlib.nullcontext(Connection())), \
             mock.patch.object(service, "_object_visibility", return_value="AVAILABLE"):
            with self.assertRaises(ExperienceProjectionError) as error:
                service._memory([candidate], [{"verification_id": "verify-fixture"}],
                                {"classifications": {"PUBLIC"}, "tags": set()})
        self.assertEqual(error.exception.reason_code, "EXPERIENCE_MEMORY_ADMISSION_BINDING_INVALID")

    def test_human_signal_counts_use_full_aggregates_not_bounded_item_arrays(self):
        reader = self._reader()
        self.addCleanup(reader.close)
        service = ExperienceProjectionService(reader, read_context=LocalOperatorReadContext())
        approvals = {"status": "AVAILABLE", "items": [], "total_count": 101, "truncated": True,
                     "decision_counts": {"APPROVE": 101}}
        verifications = {"status": "AVAILABLE", "items": [], "total_count": 101, "truncated": True,
                         "verifier_kind_counts": {"T3_HUMAN_OR_DOMAIN": 101},
                         "whole_task_verification": "UNKNOWN", "semantic_quality": "UNKNOWN"}
        with mock.patch.object(service, "_approvals", return_value=approvals), \
             mock.patch.object(service, "_verifications", return_value=verifications):
            projection = service.project_task(self.task_id)
        self.assertEqual(projection["human_signals"]["approval_decisions"]["approve_count"], 101)
        self.assertEqual(projection["human_signals"]["t3_human_or_domain_verifications"], 101)

    def test_context_and_skill_exposure_stay_unknown_even_when_loaded(self):
        serialized_size = len(json.dumps(
            {"schema_id": "nexus.context_pack", "schema_version": 1, "task_id": self.task_id,
                "run_id": self.root_run_id, "content_hash": "a" * 64, "model_visible_exposure": "UNKNOWN",
                "entries": [{"source_type": "artifact"}]},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8"))

        class ContextReader:
            def read_compiled(self, _pack_ref):
                # Match ContextPackService.read_compiled(): this is a verified
                # result envelope around the canonical persisted pack, and its
                # size describes the canonical pack rather than this envelope.
                return {"status": "CONTEXT_PACK_READ", "pack_id": "ctx-experience",
                        "task_id": self_task_id, "run_id": self_run_id,
                        "content_hash": "a" * 64, "integrity_hash": "b" * 64,
                        "serialized_byte_size": serialized_size, "model_visible_exposure": "UNKNOWN",
                        "entries": [{"source_type": "artifact"}]}

        self_task_id = self.task_id
        self_run_id = self.root_run_id

        context_rows = [{"record_id": "ctx-record", "pack_ref": "ctx-experience", "task_id": self.task_id,
                         "run_id": self.root_run_id, "content_hash": "a" * 64,
                         "serialized_byte_size": serialized_size, "state": "COMPILED", "compiled_at": self.created_at}]
        service = ExperienceProjectionService(self.writer, context_packs=ContextReader())
        context = service._contexts(context_rows, {self.root_run_id})
        self.assertEqual(context["items"][0]["model_visible_exposure"], "UNKNOWN")
        instruction = "loaded fixture instruction"
        instruction_sha256 = hashlib.sha256(instruction.encode("utf-8")).hexdigest()
        skill_row = {"selection_id": "skill-selection", "run_id": self.root_run_id,
            "task_id": self.task_id, "query_sha256": "c" * 64,
            "candidate_count": 1, "result_status": "RESOLVED", "resolution": "NEXUS_FALLBACK",
            "skill_id": "skill-fixture", "host_inventory_provenance": "ADAPTER_DISCOVERY",
            "host_native_availability": "UNKNOWN", "instruction_object_ref": "skill-instruction-fixture",
            "instruction_sha256": instruction_sha256, "instruction_byte_size": len(instruction.encode("utf-8")),
            "instruction_load_status": "LOADED_TO_GOVERNED_ARTIFACT",
            "delivery_status": "UNKNOWN", "model_visible_exposure": "UNKNOWN", "selection_latency_ms": 1.0,
            "created_at": self.created_at}
        with mock.patch.object(service, "_object_visibility", return_value="AVAILABLE"), \
                mock.patch.object(service, "_verify_skill_instruction", return_value="VERIFIED"):
            skill = service._skills([skill_row], self.task_id, {self.root_run_id: {"task_id": self.task_id}},
                                    {"classifications": {"PUBLIC"}, "tags": set()})
        self.assertEqual(skill["items"][0]["instruction_load_status"], "LOADED_TO_GOVERNED_ARTIFACT")
        self.assertEqual(skill["items"][0]["instruction_integrity"], "VERIFIED")
        self.assertEqual(skill["items"][0]["model_visible_exposure"], "UNKNOWN")
        self.assertEqual(skill["causal_effectiveness"], "UNKNOWN")

    def test_continuation_payload_schemas_are_discovered_inside_object_envelopes(self):
        previous_ref = "experience-previous-state"
        previous_hash = "e" * 64
        state_ref = "experience-continuation-state"
        state_doc = {
            "schema_id": "nexus.continuation_state", "schema_version": 1,
            "accepted_revision": "a" * 40,
            "accepted_git": {"commit_sha": "a" * 40},
            "provenance_by_fact": {"current_objective": [{"kind": "HUMAN_OPERATOR_ASSERTION"}]},
            "supersedes": {"object_id": previous_ref, "integrity_sha256": previous_hash},
            "previous_current_state": {"object_id": previous_ref, "integrity_sha256": previous_hash},
            "current_objective": "A bounded synthetic continuation test.",
            "current_operating_priority": "Verify the semantic projection.",
            "recent_work": [],
            "task_run_lifecycle_at_commit": {
                "task_id": self.task_id, "root_run_id": self.root_run_id,
                "task_status": "ACTIVE", "root_run_status": "RUNNING",
            },
            "governed_work_reference": {"task_id": self.task_id, "root_run_id": self.root_run_id},
        }
        state_payload = _canonical_json(state_doc)
        state_hash = hashlib.sha256(state_payload).hexdigest()
        delta_ref = "experience-what-changed"
        delta_doc = {
            "schema_id": "nexus.what_changed", "schema_version": 1,
            "new_state": {"object_id": state_ref, "integrity_sha256": state_hash},
            "previous_state": {"object_id": previous_ref, "integrity_sha256": previous_hash},
            "accepted_revision": {}, "objective": {}, "next_step": {}, "recent_work_added": {},
            "facts_superseded": [], "facts_unchanged": {"project_identity": "project-fixture"},
            "responsible_task": {"task_id": self.task_id, "root_run_id": self.root_run_id},
        }
        delta_payload = _canonical_json(delta_doc)
        private_payload_ref = "experience-unrelated-private-artifact"
        payloads = {
            state_ref: state_payload,
            delta_ref: delta_payload,
            private_payload_ref: b"PRIVATE MODEL OUTPUT: this body must never appear in the projection.",
        }
        rows = []
        for object_ref, payload in payloads.items():
            rows.append({
                "object_id": object_ref, "object_type": "artifact", "schema_id": "nexus.object",
                "schema_version": 1, "integrity_hash": hashlib.sha256(payload).hexdigest(),
                "created_by_run": self.root_run_id, "payload_state": "AVAILABLE",
                "subject_type": "OBJECT", "subject_ref": object_ref,
                "sensitivity_level": "PUBLIC", "handling_tags_json": "[]",
            })

        class RelationCursor:
            def fetchone(self):
                return (1,)

        class RelationConnection:
            def execute(self, *_args):
                return RelationCursor()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        class ReadStore:
            def get_payload(self, object_ref):
                return payloads[object_ref]

            @contextlib.contextmanager
            def _connection(self):
                yield RelationConnection()

        result = ExperienceProjectionService(ReadStore())._continuations(
            rows, self.task_id, {self.root_run_id}, {"classifications": {"PUBLIC"}, "tags": set()},
        )
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(result["total_count"], 1)
        self.assertEqual(result["commits"][0]["object_ref"], state_ref)
        self.assertEqual(result["commits"][0]["what_changed_ref"], delta_ref)
        self.assertEqual(result["commits"][0]["status"], "STRUCTURALLY_COMPLETE")
        self.assertEqual(result["commits"][0]["official_commit_provenance"], "NOT_VERIFIED_FROM_COMMAND_LEDGER")
        self.assertEqual(result["provenance_scope"],
                         "VISIBLE_PAYLOAD_TASK_RUN_SHAPE_ONLY; UNAVAILABLE_ARTIFACTS_ARE_POTENTIAL_CONTINUATION_CANDIDATES; OFFICIAL_COMMAND_LEDGER_NOT_VERIFIED")
        self.assertEqual(result["operator_assertion_count"], 1)
        self.assertNotIn("PRIVATE MODEL OUTPUT", json.dumps(result))

    def test_continuation_absence_is_distinct_from_purged_or_hidden_records(self):
        service = ExperienceProjectionService(self.writer)
        boundary = {"classifications": {"PUBLIC"}, "tags": set()}
        absent = service._continuations([], self.task_id, {self.root_run_id}, boundary)
        self.assertEqual(absent["status"], "NONE_RECORDED")
        self.assertEqual(absent["unavailable_candidate_count"], 0)

        def candidate(object_id, *, payload_state, sensitivity="PUBLIC"):
            return {"object_id": object_id, "object_type": "artifact", "schema_id": "nexus.object",
                    "schema_version": 1, "integrity_hash": "a" * 64, "created_by_run": self.root_run_id,
                    "payload_state": payload_state, "subject_type": "OBJECT", "subject_ref": object_id,
                    "sensitivity_level": sensitivity, "handling_tags_json": "[]"}

        purged = service._continuations(
            [candidate("continuation-purged", payload_state="PURGED")], self.task_id,
            {self.root_run_id}, boundary,
        )
        self.assertEqual(purged["status"], "PURGED")
        self.assertEqual(purged["unavailable_candidate_states"], {"PURGED": 1})

        hidden = service._continuations(
            [candidate("continuation-hidden", payload_state="AVAILABLE", sensitivity="SECRET")],
            self.task_id, {self.root_run_id}, boundary,
        )
        self.assertEqual(hidden["status"], "REDACTED")
        self.assertEqual(hidden["unavailable_candidate_states"], {"REDACTED": 1})

    def test_skill_instruction_integrity_is_verified_without_returning_instruction_body(self):
        instruction = "Private fixture instruction that must not be emitted."
        instruction_hash = hashlib.sha256(instruction.encode("utf-8")).hexdigest()

        class SkillObjectStore:
            def __init__(self, object_type, payload, created_by_run=None):
                self.object_type = object_type
                self.payload = payload
                self.created_by_run = created_by_run

            def get_object_metadata(self, _object_ref):
                return {"object_type": self.object_type, "payload_state": "AVAILABLE",
                        "created_by_run": self.created_by_run}

            def get_payload(self, _object_ref):
                return self.payload

        for schema_id, object_type, extra in (
            ("nexus.skill_instruction_snapshot", "skill", {}),
            ("nexus.skill_instruction_artifact", "artifact", {
                "source_scope": "USER", "source_namespace": "fixture", "task_id": self.task_id,
                "run_id": self.root_run_id,
            }),
        ):
            document = {"schema_id": schema_id, "schema_version": 1, "skill_id": "skill-fixture",
                        "package_revision": "b" * 64, "instruction_sha256": instruction_hash,
                        "instruction_utf8": instruction, **extra}
            payload = _canonical_json(document)
            service = ExperienceProjectionService(SkillObjectStore(
                object_type, payload, self.root_run_id if schema_id == "nexus.skill_instruction_artifact" else None))
            row = {"instruction_object_ref": "skill-instruction-fixture", "skill_id": "skill-fixture",
                   "instruction_sha256": instruction_hash, "instruction_byte_size": len(instruction.encode("utf-8")),
                   "task_id": self.task_id, "run_id": self.root_run_id}
            self.assertEqual(service._verify_skill_instruction(row), "VERIFIED")
            self.assertNotIn(instruction, json.dumps({"integrity": "VERIFIED"}))

            tampered = dict(document)
            tampered["instruction_utf8"] += "changed"
            broken = ExperienceProjectionService(SkillObjectStore(
                object_type, _canonical_json(tampered),
                self.root_run_id if schema_id == "nexus.skill_instruction_artifact" else None))
            with self.assertRaises(ExperienceProjectionError) as error:
                broken._verify_skill_instruction(row)
            self.assertEqual(error.exception.reason_code, "EXPERIENCE_SKILL_INSTRUCTION_INTEGRITY_INVALID")

    def test_project_task_reads_loaded_skill_resolution_through_integrated_sql_query(self):
        skill_id = "skill-experience-loaded"
        instruction_ref = "skill-instruction-artifact"
        instruction = "Synthetic governed skill instruction; projection must not expose this body."
        instruction_bytes = instruction.encode("utf-8")
        instruction_hash = hashlib.sha256(instruction_bytes).hexdigest()
        package_revision = "b" * 64
        document = {
            "schema_id": "nexus.skill_instruction_artifact", "schema_version": 1,
            "skill_id": skill_id, "package_revision": package_revision,
            "instruction_sha256": instruction_hash, "source_scope": "EXPLICIT_IMPORT",
            "source_namespace": "experience-test", "task_id": self.task_id,
            "run_id": self.root_run_id, "instruction_utf8": instruction,
        }
        payload = _canonical_json(document)
        self.authority.record_classification_assertion(
            self._classification("experience-skill-artifact-class", "OBJECT", instruction_ref),
            grant_id=self.grant_id, task_id=self.task_id, audience="nexus-runtime",
            command_id="experience-skill-artifact-classify",
        )
        self.writer.put_object(
            command_id="experience-skill-artifact-put", object_id=instruction_ref,
            payload=payload, object_type="artifact", created_by_run=self.root_run_id,
            classification_assertion_ref="experience-skill-artifact-class",
        )
        with self.writer._connection() as conn:
            conn.execute(
                "INSERT INTO skill_registry_entries("
                "skill_id,skill_name,description,source_scope,source_namespace,source_ref,skill_md_sha256,"
                "package_manifest_sha256,package_manifest_json,fallback_blockers_json,instruction_object_ref,"
                "registered_task_id,registered_run_id,registered_by,status,review_command_id,reviewed_by,"
                "reviewed_at,registered_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (skill_id, "loaded-fixture", "Integrated projection fixture", "EXPLICIT_IMPORT",
                 "experience-test", "loaded-fixture", instruction_hash, package_revision, "[]", "[]",
                 instruction_ref, self.task_id, self.root_run_id, "experience-host", "ENABLED",
                 "experience-skill-reviewed", "human-root", self.created_at, self.created_at),
            )
        skill_service = SkillRegistryService(
            store=self.writer, authority=self.authority,
            participation=ParticipationModeService(self.writer),
        )
        skill_service._record_resolution(
            command_id="experience-skill-resolution", task_id=self.task_id, run_id=self.root_run_id,
            query="loaded fixture", candidate_count=1,
            basis={"classification_assertion_sha256": "c" * 64, "authority_grant_id": self.grant_id},
            result_status="RESOLVED", resolution="NEXUS_FALLBACK", reason="FIXTURE",
            started=perf_counter_ns(), skill_id=skill_id, host_availability="UNAVAILABLE",
            instruction_load_status="LOADED_TO_GOVERNED_ARTIFACT", instruction_object_ref=instruction_ref,
            instruction_sha256=instruction_hash, instruction_byte_size=len(instruction_bytes),
        )

        reader = self._reader()
        self.addCleanup(reader.close)
        projection = self._project(reader)
        self.assertEqual(projection["skill_resolution"]["total_count"], 1)
        selected = projection["skill_resolution"]["items"][0]
        self.assertEqual(selected["instruction_load_status"], "LOADED_TO_GOVERNED_ARTIFACT")
        self.assertEqual(selected["instruction_integrity"], "VERIFIED")
        self.assertEqual(selected["instruction_object_ref"], instruction_ref)
        self.assertEqual(selected["model_visible_exposure"], "UNKNOWN")
        self.assertNotIn(instruction, json.dumps(projection))

    def test_skill_resolution_task_and_run_bindings_are_checked(self):
        service = ExperienceProjectionService(self.writer)
        row = {"selection_id": "skill-selection", "task_id": self.task_id,
            "run_id": self.root_run_id, "query_sha256": "c" * 64, "candidate_count": 0,
            "result_status": "NO_MATCH", "resolution": None, "skill_id": None,
            "host_inventory_provenance": None, "host_native_availability": "UNKNOWN",
            "instruction_object_ref": None, "instruction_sha256": None, "instruction_byte_size": None,
            "instruction_load_status": "NOT_LOADED", "delivery_status": "UNKNOWN",
            "model_visible_exposure": "UNKNOWN", "selection_latency_ms": 1.0,
            "created_at": self.created_at}
        boundary = {"classifications": {"PUBLIC"}, "tags": set()}
        with self.assertRaises(ExperienceProjectionError) as wrong_task:
            service._skills([dict(row, task_id="another-task")], self.task_id,
                             {self.root_run_id: {"task_id": self.task_id}}, boundary)
        self.assertEqual(wrong_task.exception.reason_code, "EXPERIENCE_SKILL_RUN_INVALID")
        with self.assertRaises(ExperienceProjectionError) as wrong_run:
            service._skills([dict(row, run_id="another-run")], self.task_id,
                             {self.root_run_id: {"task_id": self.task_id}}, boundary)
        self.assertEqual(wrong_run.exception.reason_code, "EXPERIENCE_SKILL_RUN_INVALID")

    def test_metering_v2_provenance_missing_values_and_estimate_are_preserved(self):
        service = ExperienceProjectionService(self.writer)
        participation = ParticipationModeService(self.writer)
        metering = MeteringService(self.writer, self.authority, participation)
        metering.record_host_declared(
            record_id="meter-experience", task_id=self.task_id, run_id=self.root_run_id,
            grant_id=self.grant_id, host_usage={"input_tokens": 37, "cached_input_tokens": 7},
            estimated_cost={"value": 0.004, "currency": "USD", "basis": {
                "pricing_id": "fixture", "pricing_version": "1", "formula": "synthetic estimate"}},
        )
        with self.writer._connection() as conn:
            rows = [dict(row) for row in conn.execute(
                "SELECT record_id,task_id,run_id,record_source,participation_mode,context_pack_ref,metrics_json,metrics_sha256,recorded_at "
                "FROM value_metering_records WHERE task_id=?", (self.task_id,))]
        projected = service._metering(rows, {self.root_run_id})
        metrics = projected["items"][0]["metrics"]
        self.assertEqual(metrics["host_total_turn_input_tokens"]["value"], 37)
        self.assertEqual(metrics["host_total_turn_input_tokens"]["provenance"], "HOST_DECLARED")
        self.assertEqual(metrics["model_visible_input_tokens"]["provenance"], "UNAVAILABLE")
        self.assertIsNone(metrics["model_visible_input_tokens"]["value"])
        self.assertEqual(metrics["uncached_input_tokens"]["value"], 30)
        self.assertEqual(metrics["estimated_cost"]["estimate_status"], "ESTIMATED")
        self.assertEqual(projected["metric_interpretation"], "TYPED_METRICS_PRESERVED_FREE_TEXT_REDACTED")
        poisoned = dict(rows[0])
        unsafe_metrics = json.loads(poisoned["metrics_json"])
        unsafe_metrics["host_total_turn_input_tokens"]["basis"] = "C:\\Users\\fixture\\private.txt"
        poisoned["metrics_json"] = json.dumps(unsafe_metrics, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        poisoned["metrics_sha256"] = hashlib.sha256(poisoned["metrics_json"].encode("utf-8")).hexdigest()
        scrubbed = service._metering([poisoned], {self.root_run_id})
        scrubbed_json = json.dumps(scrubbed, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("C:\\Users\\fixture", scrubbed_json)
        self.assertTrue(scrubbed["items"][0]["sensitive_text_redacted"])
        self.assertEqual(scrubbed["items"][0]["metrics"]["host_total_turn_input_tokens"]["basis"], {"present": True})

    def test_metering_untrusted_free_text_is_never_emitted(self):
        participation = ParticipationModeService(self.writer)
        metering = MeteringService(self.writer, self.authority, participation)
        metering.record_host_declared(
            record_id="meter-untrusted-text", task_id=self.task_id, run_id=self.root_run_id,
            grant_id=self.grant_id, host_usage={"input_tokens": 37},
            estimated_cost={"value": 0.004, "currency": "USD", "basis": {
                "pricing_id": "fixture", "pricing_version": "1", "formula": "safe fixture formula"}},
        )
        with self.writer._connection() as conn:
            row = dict(conn.execute(
                "SELECT record_id,task_id,run_id,record_source,participation_mode,context_pack_ref,metrics_json,"
                "metrics_sha256,recorded_at FROM value_metering_records WHERE record_id=?",
                ("meter-untrusted-text",),
            ).fetchone())
        adversarial_values = (
            "source C:\\Users\\xr\\private.txt",
            "source /home/xr/private.txt",
            "Bearer eyJhbGciOiJIUzI1NiJ9.secret.signature",
            "api_key=sk-live-secret-value",
            "https://example.invalid/?access_token=query-secret-value",
        )
        service = ExperienceProjectionService(self.writer)
        for index, unsafe in enumerate(adversarial_values):
            metrics = json.loads(row["metrics_json"])
            metrics["host_total_turn_input_tokens"]["basis"] = unsafe
            metrics["estimated_cost"]["estimation_basis"]["formula"] = unsafe
            raw = _canonical_json(metrics).decode("utf-8")
            candidate = dict(row, metrics_json=raw, metrics_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest())
            projected = service._metering([candidate], {self.root_run_id})
            encoded = json.dumps(projected, ensure_ascii=False, sort_keys=True)
            with self.subTest(index=index):
                self.assertNotIn(unsafe, encoded)
                self.assertTrue(projected["items"][0]["sensitive_text_redacted"])
                tokens = projected["items"][0]["metrics"]["host_total_turn_input_tokens"]
                self.assertEqual(tokens["value"], 37)
                self.assertEqual(tokens["provenance"], "HOST_DECLARED")
                self.assertEqual(tokens["unit"], "tokens")
                self.assertEqual(tokens["basis"], {"present": True})
                cost = projected["items"][0]["metrics"]["estimated_cost"]
                self.assertEqual(cost["value"], 0.004)
                self.assertEqual(cost["estimate_status"], "ESTIMATED")
                self.assertEqual(cost["estimation_basis"], {"present": True})

    def test_metering_observed_host_declared_derived_unavailable_and_cost_kinds_stay_separate(self):
        metrics = unavailable_metrics()
        metrics["latency_ms"] = metric(17, "OBSERVED", unit="milliseconds", basis="fixture-clock")
        metrics["host_total_turn_input_tokens"] = metric(
            45, "HOST_DECLARED", unit="tokens", basis="host-reported total input")
        metrics["uncached_input_tokens"] = metric(
            40, "DERIVED", unit="tokens", basis="host total minus cached with same source basis")
        metrics["estimated_cost"] = metric(
            0.03, "HOST_DECLARED", unit="USD", basis="host estimated cost",
            estimate_status="ESTIMATED",
            estimation_basis={"pricing_id": "fixture", "pricing_version": "1", "formula": "test-only"})
        metrics["actual_cost"] = metric(0.04, "HOST_DECLARED", unit="USD", basis="host-reported actual cost")
        raw_metrics = _canonical_json(metrics).decode("utf-8")
        row = {"record_id": "meter-mixed-provenance", "task_id": self.task_id,
               "run_id": self.root_run_id, "record_source": "HOST_DECLARED", "participation_mode": "ACTIVE",
               "context_pack_ref": None, "metrics_json": raw_metrics,
               "metrics_sha256": hashlib.sha256(raw_metrics.encode("utf-8")).hexdigest(),
               "recorded_at": self.created_at}
        result = ExperienceProjectionService(self.writer)._metering([row], {self.root_run_id})["items"][0]["metrics"]
        self.assertEqual(result["latency_ms"]["provenance"], "OBSERVED")
        self.assertEqual(result["host_total_turn_input_tokens"]["provenance"], "HOST_DECLARED")
        self.assertEqual(result["uncached_input_tokens"]["provenance"], "DERIVED")
        self.assertEqual(result["model_visible_input_tokens"]["provenance"], "UNAVAILABLE")
        self.assertEqual(result["estimated_cost"]["estimate_status"], "ESTIMATED")
        self.assertEqual(result["actual_cost"]["estimate_status"], "NOT_ESTIMATED")

    def test_host_observed_not_synthesized_and_context_memory_skill_unknowns_remain_separate(self):
        reader = self._reader()
        self.addCleanup(reader.close)
        result = self._project(reader)
        self.assertEqual(result["metering"]["status"], "UNAVAILABLE")
        self.assertEqual(result["context"]["status"], "UNAVAILABLE")
        self.assertEqual(result["context"]["model_consumption"], "UNKNOWN")
        self.assertEqual(result["skill_resolution"]["status"], "NONE_RECORDED")
        self.assertEqual(result["skill_resolution"]["model_consumption"], "UNKNOWN")
        self.assertEqual(result["memory_participation"]["status"], "NONE_RECORDED")
        self.assertEqual(result["unknowns"]["model_context_consumption"], "UNKNOWN")
        self.assertEqual(result["unknowns"]["model_skill_consumption"], "UNKNOWN")
        self.assertEqual(result["unknowns"]["skill_effectiveness"], "UNKNOWN")

    def test_cli_emits_json_via_explicit_existing_paths_without_mutating(self):
        reader = self._reader()
        before = self._snapshot_counts(reader)
        reader.close()
        before_db = self._db_commitment()
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["--data-root", str(self.data_root), "--policy", str(self.policy_path),
                         "--independent-purge-journal", str(self.journal_path),
                         "experience", self.task_id, "--json"])
        self.assertEqual(code, 0, stderr.getvalue())
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["identity"]["task_id"], self.task_id)
        after_reader = ObjectStore(self.data_root, policy=self.policy,
                                   independent_purge_journal_path=self.journal_path, read_only=True)
        self.addCleanup(after_reader.close)
        self.assertEqual(before, self._snapshot_counts(after_reader))
        self.assertEqual(before_db, self._db_commitment())
        self.assertNotIn(str(self.data_root), stdout.getvalue())

    def test_cli_uses_project_runtime_resolution_without_manual_paths(self):
        reader = self._reader()
        before = self._snapshot_counts(reader)
        reader.close()
        resolved = {"data_root": str(self.data_root), "policy_path": str(self.policy_path),
                    "independent_purge_journal_path": str(self.journal_path)}
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch("adapters.client.project_locator.resolve_project_runtime", return_value=resolved):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = main(["experience", self.task_id, "--json"])
        self.assertEqual(code, 0, stderr.getvalue())
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["identity"]["task_id"], self.task_id)
        after_reader = ObjectStore(self.data_root, policy=self.policy,
                                   independent_purge_journal_path=self.journal_path, read_only=True)
        self.addCleanup(after_reader.close)
        self.assertEqual(before, self._snapshot_counts(after_reader))

    def test_unavailable_project_binding_is_a_safe_cli_error(self):
        from adapters.client.project_locator import ProjectLocatorError

        stderr = io.StringIO()
        with mock.patch("adapters.client.project_locator.resolve_project_runtime",
                        side_effect=ProjectLocatorError("PROJECT_HOST_BINDING_MISSING")):
            with contextlib.redirect_stderr(stderr):
                code = main(["experience", self.task_id, "--json"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stderr.getvalue()), {
            "status": "DENIED_OR_FAILED", "reason": "PROJECT_HOST_BINDING_MISSING",
        })


if __name__ == "__main__":
    unittest.main()
