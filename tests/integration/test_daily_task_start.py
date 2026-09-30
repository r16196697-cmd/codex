from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from adapters.bootstrap.service import initialize_instance
from adapters.client.hosted import CodexHostedBridge
from adapters.client.task_start import (
    DailyTaskStartError,
    PROTOCOL_VERSION,
    _command_id_closure,
    _validate_plan,
    read_task_start_plan,
    start_daily_task,
)
from adapters.storage import ObjectStore
from kernel.authority import AuthorityService
from kernel.budget import BudgetService
from kernel.participation import ParticipationModeService
from kernel.run import TraceRuntime
from kernel.runtime import DeterministicRuntime
from kernel.verification import VerificationService


class _TTY(io.StringIO):
    def isatty(self):
        return True


class _NoTTY(io.StringIO):
    def isatty(self):
        return False


class DailyTaskStartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nexus-daily-task-start-")
        self.base = Path(self.temp.name)
        self.root = self.base / "instance"
        self.journal = self.base / "journal" / "instance.jsonl"
        self.journal.parent.mkdir()
        self.policy_path = self.base / "policy.json"
        policy = json.loads((Path(__file__).resolve().parents[2] / "policies/default-policy.json").read_text(encoding="utf-8"))
        policy["trust_anchors"] = ["operator-human"]
        self.policy_path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
        self.policy = policy
        initialize_instance(data_root=self.root, policy_path=self.policy_path,
                            independent_purge_journal=self.journal, command_id="fixture-initialize")
        self.store = ObjectStore(self.root, policy=policy, independent_purge_journal_path=self.journal)
        self.authority = AuthorityService(self.store, self.store.policy)
        self.authority.register_principal({
            "schema_id": "nexus.principal", "schema_version": 1,
            "principal_id": "operator-human", "principal_type": "HUMAN", "status": "ACTIVE",
        }, "fixture-human")
        self.authority.register_principal({
            "schema_id": "nexus.principal", "schema_version": 1,
            "principal_id": "runtime-service", "principal_type": "SERVICE", "status": "ACTIVE",
        }, "fixture-service")
        self.authority.register_trust_anchor({
            "schema_id": "nexus.trust_anchor", "schema_version": 1,
            "anchor_id": "operator-anchor", "principal_id": "operator-human", "policy_ref": "1",
        }, "fixture-anchor")
        self.budget = BudgetService(self.store)
        self.trace = TraceRuntime(self.store, self.authority)
        self.runtime = DeterministicRuntime(self.store, self.authority, self.budget, self.trace)
        self.verifier = VerificationService(self.store, self.authority)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    @staticmethod
    def _now_text(delta: timedelta = timedelta()) -> str:
        return (datetime.now(timezone.utc) + delta).isoformat(timespec="microseconds").replace("+00:00", "Z")

    def plan(self, label: str = "one") -> dict:
        command = "daily-start-" + label
        root_command = command + ":root"
        task = "task-" + label
        run = "run-" + label
        input_id = "input-" + label
        contract_id = "contract-" + label
        manifest_id = "manifest-" + label
        created_at = self._now_text()

        def classification(suffix, subject_type, subject_ref):
            return {
                "schema_id": "nexus.classification_assertion", "schema_version": 1,
                "assertion_id": "class-" + label + "-" + suffix,
                "subject_type": subject_type, "subject_ref": subject_ref,
                "sensitivity_level": "PUBLIC", "handling_tags": [], "policy_version": "1",
                "reason": "Operator-authorized daily Task start fixture.", "actor_id": "runtime-service",
            }

        classes = {
            "root_run": classification("run", "RUN", run),
            "root_created_event": classification("created-event", "TRACE_EVENT", "evt-" + root_command + "-root-create"),
            "input_object": classification("input", "OBJECT", input_id),
            "input_event": classification("input-event", "TRACE_EVENT", "evt-" + root_command + "-trace-input"),
            "task_contract": classification("contract", "OBJECT", contract_id),
            "root_manifest": classification("manifest", "OBJECT", manifest_id),
            "root_ready_event": classification("ready-event", "TRACE_EVENT", "evt-" + root_command + "-root-ready"),
            "root_running_event": classification("running-event", "TRACE_EVENT", "evt-" + root_command + "-root-running"),
        }
        return {
            "protocol_version": PROTOCOL_VERSION,
            "command_id": command,
            "instance_expectation": {
                "instance_id": self.store.get_instance_binding_status()["instance_id"],
                "policy_version": "1", "policy_sha256": self.store.policy_sha256,
                "journal_identity": self.store.get_instance_binding_status()["journal_identity"],
            },
            "operator_principal_id": "operator-human",
            "runtime_principal_id": "runtime-service",
            "grant": {
                "grant_id": "grant-" + label,
                "issued_at": self._now_text(timedelta(minutes=-1)),
                "expires_at": self._now_text(timedelta(days=1)),
                "action_scope": ["RUN_CREATE", "RUN_TRANSITION", "TRACE_APPEND", "OBJECT_WRITE", "CLASSIFY",
                                 "INSPECT", "VERIFY", "MEMORY_ADMIT", "MEMORY_SEARCH", "TOOL_READ"],
                "audience_scope": ["nexus-runtime", "nexus-inspect"],
                "additional_resource_scope": ["object:future-output-" + label, "run:future-work-" + label],
            },
            "root": {
                "created_at": created_at, "task_id": task, "requester_id": "operator-human",
                "root_run_id": run, "budget_account_id": "budget-" + label,
                "budget_limits": {"amount_limit": 100, "unit": "test-units", "model_call_limit": 2,
                                  "tool_call_limit": 1, "child_run_limit": 1},
                "input_object_id": input_id, "input_payload": "PRIVATE_INPUT_MARKER_" + label,
                "task_contract": {
                    "schema_id": "nexus.task_contract", "schema_version": 1,
                    "task_id": "template-task", "requester_id": "template-requester",
                    "goal": "Perform one bounded daily self-hosted work item.",
                    "constraints": ["Use the exact authorized scope."],
                    "success_criteria": ["Return a reviewable result."], "risk_class": "LOW",
                    "budget_account_ref": "template-budget",
                    "routing_constraints": {"allowed_providers": [], "forbidden_providers": [],
                                            "locality": "LOCAL_ONLY", "network_required": False, "modalities": []},
                    "routing_preferences": {"optimize_for": "BALANCED"}, "created_at": created_at,
                },
                "contract_object_id": contract_id, "dag_nodes": [], "root_manifest_object_id": manifest_id,
                "data_boundary": {"allowed_classifications": ["PUBLIC"], "handling_tags": []},
                "classifications": classes,
            },
        }

    def start(self, plan, confirmation=None):
        return start_daily_task(
            store=self.store, authority=self.authority, budget=self.budget, trace=self.trace,
            runtime=self.runtime, verifier=self.verifier, plan=plan,
            confirmation=confirmation or (lambda _phrase, _summary: True),
        )

    def counts(self):
        with self.store._connection() as conn:
            return tuple(conn.execute(
                "SELECT (SELECT COUNT(*) FROM command_ledger),(SELECT COUNT(*) FROM delegation_grants),"
                "(SELECT COUNT(*) FROM tasks),(SELECT COUNT(*) FROM runs),(SELECT COUNT(*) FROM objects),"
                "(SELECT COUNT(*) FROM trace_events),(SELECT COUNT(*) FROM budget_accounts)"
            ).fetchone())

    def assert_no_new_task_start_mutation(self, plan, reason, confirmation=None):
        before = self.counts()
        called = []
        with self.assertRaises(DailyTaskStartError) as caught:
            self.start(plan, confirmation=confirmation or (lambda *_: called.append(True) or True))
        self.assertEqual(caught.exception.reason_code, reason)
        self.assertEqual(self.counts(), before)
        self.assertEqual(called, [])

    def test_valid_confirmation_creates_exact_grant_and_hosted_root(self):
        plan = self.plan()
        confirmations = []
        result = self.start(plan, confirmation=lambda phrase, summary: confirmations.append((phrase, summary)) or phrase == "START task-one")
        self.assertEqual(result, {
            "status": "DAILY_TASK_STARTED", "task_id": "task-one", "root_run_id": "run-one",
            "grant_id": "grant-one", "grant_expires_at": plan["grant"]["expires_at"],
            "executor_kind": "ORCHESTRATOR", "run_status": "RUNNING",
        })
        self.assertEqual(confirmations[0][0], "START task-one")
        summary = confirmations[0][1]
        self.assertEqual(summary["root_created_at"], plan["root"]["created_at"])
        self.assertEqual(summary["grant_issued_at"], plan["grant"]["issued_at"])
        self.assertNotIn("PRIVATE_INPUT_MARKER", json.dumps(summary))
        self.assertNotIn(str(self.base), json.dumps(summary))
        with self.store._connection() as conn:
            grant = conn.execute("SELECT * FROM delegation_grants WHERE grant_id='grant-one'").fetchone()
            task = conn.execute("SELECT requester_id,status,root_run_id,created_at FROM tasks WHERE task_id='task-one'").fetchone()
            run = conn.execute("SELECT task_id,status,executor_kind,parent_run_id,grant_id,manifest_ref,created_at FROM runs WHERE run_id='run-one'").fetchone()
            contract = conn.execute("SELECT current_object_id FROM logical_refs WHERE ref_id='task-contract:task-one'").fetchone()
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM subtasks WHERE task_id='task-one'").fetchone()[0], 0)
            request = conn.execute("SELECT operation,result_json FROM command_ledger WHERE command_id='daily-start-one:request'").fetchone()
            ledger_text = " ".join(row[0] for row in conn.execute("SELECT result_json FROM command_ledger"))
        self.assertIsNone(grant["parent_grant_id"])
        self.assertEqual(json.loads(grant["task_scope_json"]), ["task-one"])
        self.assertEqual(json.loads(grant["resource_scope_json"]), sorted(set(self.expected_resources(plan))))
        self.assertEqual(json.loads(grant["action_scope_json"]), sorted(plan["grant"]["action_scope"]))
        self.assertEqual(json.loads(grant["audience_scope_json"]), sorted(plan["grant"]["audience_scope"]))
        self.assertEqual(task["status"], "ACTIVE")
        self.assertEqual(task["requester_id"], "operator-human")
        self.assertEqual(task["root_run_id"], "run-one")
        self.assertEqual(tuple(run), ("task-one", "RUNNING", "ORCHESTRATOR", None, "grant-one", "manifest-one", plan["root"]["created_at"]))
        self.assertEqual(task["created_at"], plan["root"]["created_at"])
        contract_doc = json.loads(self.store.get_payload("contract-one"))
        self.assertEqual(contract_doc["created_at"], plan["root"]["created_at"])
        self.assertEqual(contract["current_object_id"], "contract-one")
        self.assertEqual(request["operation"], "daily_task_start_request")
        self.assertEqual(json.loads(request["result_json"]), {"status": "REQUEST_BOUND"})
        self.assertNotIn("PRIVATE_INPUT_MARKER", ledger_text)
        self.assertEqual(self.store.get_payload("input-one"), b"PRIVATE_INPUT_MARKER_one")
        self.assertEqual(self.store.get_object_metadata("contract-one")["object_type"], "task_contract")
        self.assertEqual(self.store.get_object_metadata("manifest-one")["object_type"], "run_manifest")

    def expected_resources(self, plan):
        root = plan["root"]
        root_command = plan["command_id"] + ":root"
        refs = {
            "task:" + root["task_id"], root["root_run_id"], root["input_object_id"],
            root["contract_object_id"], root["root_manifest_object_id"],
            "evt-" + root_command + "-root-create", "evt-" + root_command + "-trace-input",
            "evt-" + root_command + "-root-ready", "evt-" + root_command + "-root-running",
            *plan["grant"]["additional_resource_scope"],
        }
        return sorted(refs)

    @staticmethod
    def _parse_timestamp(value):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    @staticmethod
    def _format_timestamp(value):
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")

    def test_root_created_at_must_fall_inside_grant_interval(self):
        before_issued = self.plan("created-before-issued")
        issued = self._parse_timestamp(before_issued["grant"]["issued_at"])
        invalid_created = self._format_timestamp(issued - timedelta(microseconds=1))
        before_issued["root"]["created_at"] = invalid_created
        before_issued["root"]["task_contract"]["created_at"] = invalid_created
        self.assert_no_new_task_start_mutation(before_issued, "DAILY_TASK_CREATED_AT_INVALID")

        after_expiry = self.plan("created-after-expiry")
        invalid_created = after_expiry["grant"]["expires_at"]
        after_expiry["root"]["created_at"] = invalid_created
        after_expiry["root"]["task_contract"]["created_at"] = invalid_created
        self.assert_no_new_task_start_mutation(after_expiry, "DAILY_TASK_CREATED_AT_INVALID")

    def test_task_contract_created_at_must_match_root_created_at(self):
        plan = self.plan("created-mismatch")
        root_created = self._parse_timestamp(plan["root"]["created_at"])
        plan["root"]["task_contract"]["created_at"] = self._format_timestamp(
            root_created - timedelta(microseconds=1),
        )
        self.assert_no_new_task_start_mutation(plan, "DAILY_TASK_CREATED_AT_MISMATCH")

    def test_future_root_created_at_is_denied_before_confirmation(self):
        plan = self.plan("future-created")
        future = self._format_timestamp(datetime.now(timezone.utc) + timedelta(minutes=5))
        plan["root"]["created_at"] = future
        plan["root"]["task_contract"]["created_at"] = future
        self.assert_no_new_task_start_mutation(plan, "DAILY_TASK_CREATED_AT_NOT_CURRENT")

    def test_denied_confirmation_and_non_tty_are_zero_mutation(self):
        self.assert_no_new_task_start_mutation(self.plan("deny"), "DAILY_TASK_CONFIRMATION_DENIED",
                                               confirmation=lambda *_: False)
        from adapters.client.__main__ import _confirm_daily_task_start
        before = self.counts()
        with mock.patch("sys.stdin", _NoTTY()), mock.patch("sys.stdout", _NoTTY()):
            with self.assertRaises(DailyTaskStartError) as caught:
                _confirm_daily_task_start("START task-tty", {})
        self.assertEqual(caught.exception.reason_code, "INTERACTIVE_TTY_REQUIRED")
        self.assertEqual(self.counts(), before)

    def test_existing_root_cli_requires_tty_and_cannot_accept_a_yes_bypass(self):
        from adapters.client.__main__ import main

        plan = self.plan("cli-nontty")
        plan_path = self.base / "cli-nontty-plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        argv = ["--data-root", str(self.root), "--policy", str(self.policy_path),
                "--independent-purge-journal", str(self.journal), "task", "start", "--plan", str(plan_path)]
        before = self.counts()
        self.store.close()
        output, errors = _NoTTY(), _NoTTY()
        with (mock.patch("sys.stdin", _NoTTY()), mock.patch("sys.stdout", output),
              mock.patch("sys.stderr", errors), mock.patch("adapters.client.__main__._runtime") as runtime_open):
            code = main(argv)
            runtime_open.assert_not_called()
        self.store = ObjectStore(self.root, policy=self.policy, independent_purge_journal_path=self.journal)
        self.authority = AuthorityService(self.store, self.store.policy)
        self.budget = BudgetService(self.store)
        self.trace = TraceRuntime(self.store, self.authority)
        self.runtime = DeterministicRuntime(self.store, self.authority, self.budget, self.trace)
        self.verifier = VerificationService(self.store, self.authority)
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(errors.getvalue())["reason"], "INTERACTIVE_TTY_REQUIRED")
        self.assertEqual(self.counts(), before)

        with mock.patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                main(argv + ["--yes"])
        self.assertEqual(self.counts(), before)

    def test_non_normal_mode_inactive_participation_and_inactive_service_are_rejected(self):
        plan = self.plan("safe-mode")
        with mock.patch("adapters.client.task_start.RuntimeModeService.current", return_value={"mode": "SAFE"}):
            self.assert_no_new_task_start_mutation(plan, "DAILY_TASK_RUNTIME_MODE_UNSUPPORTED")
        plan = self.plan("observe-mode")
        with mock.patch("adapters.client.task_start.ParticipationModeService.current", return_value={"mode": "OBSERVE"}):
            self.assert_no_new_task_start_mutation(plan, "DAILY_TASK_PARTICIPATION_INACTIVE")
        self.authority.revoke_principal("runtime-service", "fixture-revoke-service")
        self.assert_no_new_task_start_mutation(self.plan("inactive-service"), "DAILY_TASK_RUNTIME_PRINCIPAL_INVALID")

    def test_binding_runtime_and_identity_preflights_are_zero_mutation(self):
        wrong = self.plan("wrong-instance")
        wrong["instance_expectation"]["instance_id"] = "another-instance"
        self.assert_no_new_task_start_mutation(wrong, "DAILY_TASK_INSTANCE_BINDING_MISMATCH")

        future = self.plan("future")
        future["grant"]["issued_at"] = self._now_text(timedelta(minutes=5))
        future["grant"]["expires_at"] = self._now_text(timedelta(days=2))
        created_after_issue = self._format_timestamp(
            self._parse_timestamp(future["grant"]["issued_at"]) + timedelta(seconds=1),
        )
        future["root"]["created_at"] = created_after_issue
        future["root"]["task_contract"]["created_at"] = created_after_issue
        self.assert_no_new_task_start_mutation(future, "DAILY_TASK_GRANT_NOT_CURRENT")
        expired = self.plan("expired")
        expired["grant"]["issued_at"] = self._now_text(timedelta(days=-2))
        expired["grant"]["expires_at"] = self._now_text(timedelta(days=-1))
        created_inside_interval = self._format_timestamp(
            self._parse_timestamp(expired["grant"]["issued_at"]) + timedelta(minutes=1),
        )
        expired["root"]["created_at"] = created_inside_interval
        expired["root"]["task_contract"]["created_at"] = created_inside_interval
        self.assert_no_new_task_start_mutation(expired, "DAILY_TASK_GRANT_NOT_CURRENT")

        plan = self.plan("bad-operator-type")
        plan["operator_principal_id"] = "runtime-service"
        self.authority.register_principal({
            "schema_id": "nexus.principal", "schema_version": 1,
            "principal_id": "other-service", "principal_type": "SERVICE", "status": "ACTIVE",
        }, "fixture-other-service")
        plan["operator_principal_id"] = "other-service"
        plan["root"]["requester_id"] = "other-service"
        self.assert_no_new_task_start_mutation(plan, "DAILY_TASK_OPERATOR_UNTRUSTED")

        self.authority.revoke_principal("operator-human", "fixture-revoke-human")
        self.assert_no_new_task_start_mutation(self.plan("inactive-operator"), "DAILY_TASK_OPERATOR_UNTRUSTED")

    def test_untrusted_human_anchor_and_non_service_runtime_are_rejected(self):
        self.authority.register_principal({
            "schema_id": "nexus.principal", "schema_version": 1,
            "principal_id": "other-human", "principal_type": "HUMAN", "status": "ACTIVE",
        }, "fixture-other-human")
        untrusted = self.plan("untrusted")
        untrusted["operator_principal_id"] = "other-human"
        untrusted["root"]["requester_id"] = "other-human"
        self.assert_no_new_task_start_mutation(untrusted, "DAILY_TASK_OPERATOR_UNTRUSTED")

        plan = self.plan("wrong-runtime-type")
        plan["runtime_principal_id"] = "other-human"
        for assertion in plan["root"]["classifications"].values():
            assertion["actor_id"] = "other-human"
        self.assert_no_new_task_start_mutation(plan, "DAILY_TASK_RUNTIME_PRINCIPAL_INVALID")

    def test_scopes_are_closed_sorted_and_resource_scope_is_derived(self):
        plan = self.plan("scope")
        normalized = _validate_plan(self.store, self.authority, plan)
        self.assertEqual(normalized["grant"]["resource_scope"], self.expected_resources(plan))
        self.assertEqual(normalized["grant"]["action_scope"], sorted(set(plan["grant"]["action_scope"])))
        self.assertEqual(normalized["grant"]["audience_scope"], sorted(set(plan["grant"]["audience_scope"])))
        self.assertEqual(len(normalized["command_ids"]), len(set(normalized["command_ids"])))
        self.assertEqual(set(normalized["grant"]["resource_scope"]), set(self.expected_resources(plan)))

    def test_wildcards_forbidden_actions_audiences_and_paths_are_rejected_before_binding(self):
        cases = []
        p = self.plan("wild-resource")
        p["grant"]["additional_resource_scope"] = ["*"]
        cases.append((p, "DAILY_TASK_RESOURCE_SCOPE_INVALID"))
        p = self.plan("wild-action")
        p["grant"]["action_scope"].append("*")
        cases.append((p, "DAILY_TASK_FORBIDDEN_ACTION"))
        p = self.plan("forbidden-action")
        p["grant"]["action_scope"].append("DELEGATE")
        cases.append((p, "DAILY_TASK_FORBIDDEN_ACTION"))
        p = self.plan("wild-audience")
        p["grant"]["audience_scope"].append("*")
        cases.append((p, "DAILY_TASK_AUDIENCE_SCOPE_INVALID"))
        p = self.plan("path-resource")
        p["grant"]["additional_resource_scope"] = ["C:\\private\\secret"]
        cases.append((p, "DAILY_TASK_RESOURCE_SCOPE_INVALID"))
        for plan, reason in cases:
            with self.subTest(reason=reason, command=plan["command_id"]):
                self.assert_no_new_task_start_mutation(plan, reason)

    def test_invalid_task_contract_and_nonempty_dag_fail_before_request_binding(self):
        p = self.plan("invalid-contract")
        del p["root"]["task_contract"]["goal"]
        self.assert_no_new_task_start_mutation(p, "DAILY_TASK_ROOT_SCHEMA_INVALID")
        p = self.plan("nonempty-dag")
        p["root"]["dag_nodes"] = [{"node_id": "child"}]
        self.assert_no_new_task_start_mutation(p, "DAILY_TASK_DAG_UNSUPPORTED")
        p = self.plan("class-subject-mismatch")
        p["root"]["classifications"]["input_object"]["subject_ref"] = "other-input"
        self.assert_no_new_task_start_mutation(p, "DAILY_TASK_CLASSIFICATION_INVALID")

    def test_duplicate_unknown_nonstandard_and_invalid_json_plan_input_is_rejected(self):
        parser_cases = (
            (b'{"x":1,"x":2}',),
            (b'{"x":NaN}',),
            (b'[]',),
            (b'\xff',),
        )
        path = self.base / "strict-plan.json"
        for (raw,) in parser_cases:
            path.write_bytes(raw)
            with self.assertRaises(DailyTaskStartError) as caught:
                read_task_start_plan(path)
            self.assertEqual(caught.exception.reason_code, "DAILY_TASK_PLAN_INVALID")
        p = self.plan("unknown"); p["unexpected"] = True
        self.assert_no_new_task_start_mutation(p, "DAILY_TASK_PLAN_INVALID")

    def test_task_scope_is_single_exact_task_and_no_parent_grant(self):
        result = self.start(self.plan("scope-semantics"))
        self.assertEqual(result["task_id"], "task-scope-semantics")
        with self.store._connection() as conn:
            grant = conn.execute("SELECT parent_grant_id,task_scope_json FROM delegation_grants WHERE grant_id='grant-scope-semantics'").fetchone()
        self.assertIsNone(grant["parent_grant_id"])
        self.assertEqual(json.loads(grant["task_scope_json"]), ["task-scope-semantics"])

    def test_exact_retry_after_request_binding_does_not_prompt_and_resumes_grant_and_root(self):
        class SimulatedProcessLoss(BaseException):
            pass

        plan = self.plan("after-bind")
        original = self.authority.create_grant

        def lose_after_binding(*_args, **_kwargs):
            raise SimulatedProcessLoss()

        with mock.patch.object(AuthorityService, "create_grant", lose_after_binding):
            with self.assertRaises(SimulatedProcessLoss):
                self.start(plan)
        with self.store._connection() as conn:
            self.assertIsNotNone(conn.execute("SELECT 1 FROM command_ledger WHERE command_id='daily-start-after-bind:request'").fetchone())
            self.assertIsNone(conn.execute("SELECT 1 FROM delegation_grants WHERE grant_id='grant-after-bind'").fetchone())
        prompts = []
        with mock.patch.object(AuthorityService, "create_grant", original):
            historical_retry_time = self._parse_timestamp(plan["root"]["created_at"]) + timedelta(minutes=5)
            with mock.patch("adapters.client.task_start._now", return_value=historical_retry_time):
                result = self.start(plan, confirmation=lambda *_: prompts.append(True) or False)
        self.assertEqual(prompts, [])
        self.assertEqual(result["status"], "DAILY_TASK_STARTED")
        self.assertEqual(self.store.get_instance_binding_status()["state"], "FRESH_BOUND_INSTANCE")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM delegation_grants WHERE grant_id='grant-after-bind'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tasks WHERE task_id='task-after-bind'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM runs WHERE run_id='run-after-bind'").fetchone()[0], 1)

    def test_exact_retry_after_grant_commit_reuses_grant_and_completes_root(self):
        class SimulatedProcessLoss(BaseException):
            pass

        plan = self.plan("after-grant")
        original = CodexHostedBridge.create_task_root

        def lose_after_grant(*_args, **_kwargs):
            raise SimulatedProcessLoss()

        with mock.patch.object(CodexHostedBridge, "create_task_root", lose_after_grant):
            with self.assertRaises(SimulatedProcessLoss):
                self.start(plan)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM delegation_grants WHERE grant_id='grant-after-grant'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tasks WHERE task_id='task-after-grant'").fetchone()[0], 0)
        historical_retry_time = self._parse_timestamp(plan["root"]["created_at"]) + timedelta(minutes=5)
        with mock.patch("adapters.client.task_start._now", return_value=historical_retry_time):
            result = self.start(plan, confirmation=lambda *_: self.fail("exact bound retry prompted again"))
        self.assertEqual(result["status"], "DAILY_TASK_STARTED")
        with self.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM delegation_grants WHERE grant_id='grant-after-grant'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tasks WHERE task_id='task-after-grant'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM runs WHERE run_id='run-after-grant'").fetchone()[0], 1)

    def test_completed_exact_retry_is_historical_and_changed_request_conflicts(self):
        plan = self.plan("completed")
        prompts = []
        expected = self.start(plan, confirmation=lambda *args: prompts.append(args) or True)
        before = self.counts()
        after_expiry = self._parse_timestamp(plan["grant"]["expires_at"]) + timedelta(seconds=1)
        with mock.patch("adapters.client.task_start._now", return_value=after_expiry):
            replay = self.start(plan, confirmation=lambda *_: self.fail("completed exact replay requested confirmation"))
        self.assertEqual(replay, expected)
        self.assertEqual(prompts.__len__(), 1)
        self.assertEqual(self.counts(), before)

        changed = copy.deepcopy(plan)
        changed["root"]["input_payload"] += " changed"
        with self.assertRaises(DailyTaskStartError) as caught:
            self.start(changed, confirmation=lambda *_: self.fail("retargeted request prompted"))
        self.assertEqual(caught.exception.reason_code, "COMMAND_CONFLICT")
        self.assertEqual(self.counts(), before)

    def test_incompatible_existing_grant_and_preexisting_child_command_collision_fail_closed(self):
        p = self.plan("grant-conflict")
        grant = {
            "schema_id": "nexus.delegation_grant", "schema_version": 1,
            "grant_id": p["grant"]["grant_id"], "issued_by": "operator-human", "granted_to": "runtime-service",
            "task_scope": ["other-task"], "resource_scope": ["other-resource"],
            "action_scope": ["RUN_CREATE"], "audience_scope": ["nexus-runtime"],
            "issued_at": self._now_text(timedelta(minutes=-1)), "expires_at": self._now_text(timedelta(days=1)),
            "status": "ACTIVE", "policy_version": "1",
        }
        self.authority.create_grant(grant, "fixture-conflicting-grant")
        self.assert_no_new_task_start_mutation(p, "DAILY_TASK_LOGICAL_ID_CONFLICT")

        p = self.plan("child-collision")
        colliding = p["command_id"] + ":root-bind-contract-object"
        self.store.bind_command_request(command_id=colliding, operation="fixture_other_command", request={"bound": True})
        before = self.counts()
        with self.assertRaises(DailyTaskStartError) as caught:
            self.start(p)
        self.assertEqual(caught.exception.reason_code, "DAILY_TASK_COMMAND_ID_CONFLICT")
        self.assertEqual(self.counts(), before)
        with self.store._connection() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM command_ledger WHERE command_id='daily-start-child-collision:request'").fetchone())

    def test_root_resource_closure_includes_every_bridge_classification_and_explicit_additions(self):
        plan = self.plan("resources")
        normalized = _validate_plan(self.store, self.authority, plan)
        root_command = plan["command_id"] + ":root"
        expected = {
            "task:" + plan["root"]["task_id"], plan["root"]["root_run_id"],
            plan["root"]["input_object_id"], plan["root"]["contract_object_id"],
            plan["root"]["root_manifest_object_id"],
            "evt-" + root_command + "-root-create", "evt-" + root_command + "-trace-input",
            "evt-" + root_command + "-root-ready", "evt-" + root_command + "-root-running",
            *plan["grant"]["additional_resource_scope"],
        }
        self.assertEqual(set(normalized["grant"]["resource_scope"]), expected)
        self.assertEqual(normalized["grant"]["resource_scope"], sorted(expected))

    def test_cli_wires_task_start_and_sanitizes_confirmation_and_result(self):
        from adapters.client.__main__ import main

        plan = self.plan("cli")
        path = self.base / "operator-plan.json"
        path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        old_store = self.store
        old_store.close()
        argv = ["--data-root", str(self.root), "--policy", str(self.policy_path),
                "--independent-purge-journal", str(self.journal), "task", "start", "--plan", str(path)]
        input_tty, output, errors = _TTY("START task-cli\n"), _TTY(), _TTY()
        with mock.patch("sys.stdin", input_tty), mock.patch("sys.stdout", output), mock.patch("sys.stderr", errors):
            code = main(argv)
        self.store = ObjectStore(self.root, policy=self.policy, independent_purge_journal_path=self.journal)
        self.authority = AuthorityService(self.store, self.store.policy)
        self.budget = BudgetService(self.store)
        self.trace = TraceRuntime(self.store, self.authority)
        self.runtime = DeterministicRuntime(self.store, self.authority, self.budget, self.trace)
        self.verifier = VerificationService(self.store, self.authority)
        self.assertEqual(code, 0)
        rendered = output.getvalue()
        self.assertIn("DAILY_TASK_STARTED", rendered)
        self.assertIn("START task-cli", rendered)
        self.assertNotIn("PRIVATE_INPUT_MARKER", rendered)
        self.assertNotIn(str(path), rendered)
        self.assertEqual(errors.getvalue(), "")

    def test_cli_missing_root_does_not_initialize_and_non_tty_denies(self):
        from adapters.client.__main__ import main

        missing = self.base / "missing-root"
        journal = self.base / "journal" / "missing.jsonl"
        plan_path = self.base / "missing-plan.json"
        plan_path.write_text("{}", encoding="utf-8")
        argv = ["--data-root", str(missing), "--policy", str(self.policy_path),
                "--independent-purge-journal", str(journal), "task", "start", "--plan", str(plan_path)]
        output, errors = _NoTTY(), _NoTTY()
        with mock.patch("sys.stdin", output), mock.patch("sys.stdout", output), mock.patch("sys.stderr", errors):
            code = main(argv)
        self.assertEqual(code, 2)
        self.assertFalse(missing.exists())
        self.assertNotIn(str(missing), errors.getvalue())


if __name__ == "__main__":
    unittest.main()
