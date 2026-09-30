from __future__ import annotations

import contextlib
import copy
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from adapters.client.task_finish import (
    DailyTaskFinishError,
    PROTOCOL_VERSION,
    finish_daily_task,
    read_task_finish_plan,
)
class _TTY(io.StringIO):
    def isatty(self):
        return True


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _ClosureProjectionStore:
    def __init__(self, validate, children=(), effects=()):
        self._validate = validate
        self.children = list(children)
        self.effects = list(effects)

    @contextlib.contextmanager
    def _connection(self):
        class Connection:
            def __init__(inner):
                inner.executed = []

            def execute(inner, sql, params=()):
                inner.executed.append((sql, params))
                if "FROM runs WHERE task_id" in sql:
                    return _Rows([{"status": status} for status in self.children])
                if "FROM effects e JOIN runs" in sql:
                    return _Rows(self.effects)
                raise AssertionError("unexpected closure projection query")

        yield Connection()


class DailyTaskFinishTests(unittest.TestCase):
    """Use a disposable existing instance and the accepted Task Start/Core APIs."""

    def setUp(self):
        from tests.integration.test_daily_task_start import DailyTaskStartTests

        self.fixture = DailyTaskStartTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    @staticmethod
    def _now_text(delta: timedelta = timedelta()) -> str:
        return (datetime.now(timezone.utc) + delta).isoformat(timespec="microseconds").replace("+00:00", "Z")

    def start_task(self, label: str = "finish"):
        plan = self.fixture.plan(label)
        command = "finish-command-" + label
        extra = ["evt-" + command + ":verifying", "evt-" + command + ":terminal"]
        plan["grant"]["additional_resource_scope"] = sorted(set(plan["grant"]["additional_resource_scope"] + extra))
        self.fixture.start(plan)
        return plan, command

    def finish_plan(self, start_plan, command: str, outcome: str = "SUCCEEDED"):
        def assertion(suffix, ref):
            return {
                "schema_id": "nexus.classification_assertion", "schema_version": 1,
                "assertion_id": "finish-class-" + start_plan["root"]["task_id"] + "-" + suffix,
                "subject_type": "TRACE_EVENT", "subject_ref": ref,
                "sensitivity_level": "PUBLIC", "handling_tags": [], "policy_version": "1",
                "reason": "Operator-authorized Task finish event.", "actor_id": "runtime-service",
            }

        classes = {"terminal_event": assertion("terminal", "evt-" + command + ":terminal")}
        if outcome == "SUCCEEDED":
            classes["verifying_event"] = assertion("verifying", "evt-" + command + ":verifying")
        return {
            "protocol_version": PROTOCOL_VERSION,
            "command_id": command,
            "instance_expectation": copy.deepcopy(start_plan["instance_expectation"]),
            "operator_principal_id": "operator-human",
            "task_id": start_plan["root"]["task_id"],
            "root_run_id": start_plan["root"]["root_run_id"],
            "grant_id": start_plan["grant"]["grant_id"],
            "outcome": outcome,
            "classifications": classes,
        }

    def finish(self, plan, confirmation=None):
        return finish_daily_task(
            store=self.fixture.store, authority=self.fixture.authority, trace=self.fixture.trace,
            plan=plan, confirmation=confirmation or (lambda _phrase, _summary: True),
        )

    def counts(self):
        with self.fixture.store._connection() as conn:
            return tuple(conn.execute(
                "SELECT (SELECT COUNT(*) FROM command_ledger),(SELECT COUNT(*) FROM delegation_grants),"
                "(SELECT COUNT(*) FROM tasks),(SELECT COUNT(*) FROM runs),(SELECT COUNT(*) FROM objects),"
                "(SELECT COUNT(*) FROM trace_events),(SELECT COUNT(*) FROM authority_events),"
                "(SELECT COUNT(*) FROM classification_assertions)"
            ).fetchone())

    def test_success_runs_verifying_then_terminal_then_revokes_original_grant(self):
        start, command = self.start_task()
        plan = self.finish_plan(start, command)
        confirmations = []
        result = self.finish(plan, lambda phrase, summary: confirmations.append((phrase, summary)) or True)
        self.assertEqual(result, {
            "status": "DAILY_TASK_FINISHED", "task_id": "task-finish", "root_run_id": "run-finish",
            "outcome": "SUCCEEDED", "task_status": "SUCCEEDED", "run_status": "SUCCEEDED",
            "grant_id": "grant-finish", "grant_status": "REVOKED",
        })
        self.assertEqual(confirmations[0][0], "FINISH task-finish SUCCEEDED")
        self.assertEqual(confirmations[0][1]["child_active_count"], 0)
        self.assertEqual(confirmations[0][1]["unresolved_effect_count"], 0)
        with self.fixture.store._connection() as conn:
            rows = conn.execute("SELECT event_json FROM trace_events WHERE run_id='run-finish' ORDER BY seq_no").fetchall()
            grant = conn.execute("SELECT status,parent_grant_id FROM delegation_grants WHERE grant_id='grant-finish'").fetchone()
            ledger = {row["command_id"]: row["operation"] for row in conn.execute(
                "SELECT command_id,operation FROM command_ledger WHERE command_id LIKE ?", (command + ":%",)
            )}
        transitioned = [json.loads(row["event_json"]).get("typed_metadata", {}).get("to_status")
                        for row in rows if json.loads(row["event_json"]).get("event_type") == "nexus.run.transitioned"]
        self.assertEqual(transitioned[-2:], ["VERIFYING", "SUCCEEDED"])
        self.assertEqual(tuple(grant), ("REVOKED", None))
        self.assertEqual(ledger[command + ":request"], "daily_task_finish_request")
        self.assertEqual(ledger[command + ":verifying"], "transition_run")
        self.assertEqual(ledger[command + ":terminal"], "transition_run")
        self.assertEqual(ledger[command + ":revoke"], "transition_grant")

    def test_failed_and_cancelled_use_direct_terminal_transition(self):
        for label, outcome in (("failed", "FAILED"), ("cancelled", "CANCELLED")):
            with self.subTest(outcome=outcome):
                start, command = self.start_task(label)
                result = self.finish(self.finish_plan(start, command, outcome))
                self.assertEqual(result["outcome"], outcome)
                self.assertEqual(result["task_status"], outcome)
                self.assertEqual(result["run_status"], outcome)
                with self.fixture.store._connection() as conn:
                    transitions = [json.loads(row[0])["typed_metadata"]["to_status"] for row in conn.execute(
                        "SELECT event_json FROM trace_events WHERE run_id=? AND event_type='nexus.run.transitioned' ORDER BY seq_no",
                        (start["root"]["root_run_id"],),
                    )]
                self.assertEqual(transitions[-1], outcome)
                self.assertNotIn("VERIFYING", transitions)

    def test_denied_confirmation_is_zero_finish_mutation(self):
        start, command = self.start_task()
        plan = self.finish_plan(start, command)
        before = self.counts()
        with self.assertRaises(DailyTaskFinishError) as caught:
            self.finish(plan, lambda *_: False)
        self.assertEqual(caught.exception.reason_code, "DAILY_TASK_FINISH_CONFIRMATION_DENIED")
        self.assertEqual(self.counts(), before)

    def test_wrong_binding_and_missing_finish_resource_fail_before_confirmation(self):
        start, command = self.start_task("wrong-binding")
        plan = self.finish_plan(start, command)
        plan["instance_expectation"]["instance_id"] = "wrong-instance"
        before = self.counts()
        called = []
        with self.assertRaises(DailyTaskFinishError) as caught:
            self.finish(plan, lambda *_: called.append(True) or True)
        self.assertEqual(caught.exception.reason_code, "DAILY_TASK_FINISH_INSTANCE_BINDING_MISMATCH")
        self.assertEqual(self.counts(), before)
        self.assertEqual(called, [])

        start, command = self.start_task("missing-resource")
        start["grant"]["additional_resource_scope"] = []
        # The Grant is already persisted, so use a different finish command ID
        # whose event resource was not pre-authorized at Task Start.
        plan = self.finish_plan(start, "other-finish-command-missing-resource")
        before = self.counts()
        with self.assertRaises(DailyTaskFinishError) as caught:
            self.finish(plan)
        self.assertEqual(caught.exception.reason_code, "DAILY_TASK_FINISH_SCOPE_NOT_PREAUTHORIZED")
        self.assertEqual(self.counts(), before)

    def test_inactive_operator_fails_before_confirmation(self):
        start, command = self.start_task("inactive-operator")
        plan = self.finish_plan(start, command)
        self.fixture.authority.revoke_principal("operator-human", "fixture-revoke-operator")
        before = self.counts()
        called = []
        with self.assertRaises(DailyTaskFinishError) as caught:
            self.finish(plan, lambda *_: called.append(True) or True)
        self.assertEqual(caught.exception.reason_code, "DAILY_TASK_FINISH_OPERATOR_UNTRUSTED")
        self.assertEqual(self.counts(), before)
        self.assertEqual(called, [])

    def test_expired_or_revoked_original_grant_is_not_replaced(self):
        start, command = self.start_task("expired")
        plan = self.finish_plan(start, command)
        before = self.counts()
        with mock.patch("kernel.authority.service._now", return_value=datetime.now(timezone.utc) + timedelta(days=3)):
            with self.assertRaises(DailyTaskFinishError) as caught:
                self.finish(plan)
        self.assertEqual(caught.exception.reason_code, "DAILY_TASK_FINISH_AUTHORITY_UNAVAILABLE")
        self.assertEqual(self.counts(), before)
        with self.fixture.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM delegation_grants").fetchone()[0], 1)

        start, command = self.start_task("revoked")
        self.fixture.authority.revoke_grant("grant-revoked", "fixture-revoke-original")
        plan = self.finish_plan(start, command)
        before = self.counts()
        with self.assertRaises(DailyTaskFinishError) as caught:
            self.finish(plan)
        self.assertEqual(caught.exception.reason_code, "DAILY_TASK_FINISH_AUTHORITY_UNAVAILABLE")
        self.assertEqual(self.counts(), before)

    def test_active_child_run_blocks_finish(self):
        start, command = self.start_task("child")
        # The narrow child-state gate is exercised with a read-only projection
        # stub: this Task Start contract does not authorize child creation.
        before = self.counts()
        with mock.patch("adapters.client.task_finish._child_active_count", return_value=1):
            with self.assertRaises(DailyTaskFinishError) as caught:
                self.finish(self.finish_plan(start, command))
        self.assertEqual(caught.exception.reason_code, "DAILY_TASK_CHILD_WORK_ACTIVE")
        self.assertEqual(self.counts(), before)

    def test_child_and_effect_closure_use_current_terminal_semantics(self):
        from adapters.client.task_finish import _check_work_closure, _unresolved_effect_count

        store = _ClosureProjectionStore(self.fixture.store._validate, children=("RUNNING",))
        with self.assertRaises(DailyTaskFinishError) as caught:
            _check_work_closure(store, "task-x")
        self.assertEqual(caught.exception.reason_code, "DAILY_TASK_CHILD_WORK_ACTIVE")

        store = _ClosureProjectionStore(self.fixture.store._validate, children=("FAILED",))
        self.assertEqual(_check_work_closure(store, "task-x"), (0, 0))

        effect = {
            "schema_id": "nexus.effect", "schema_version": 1, "effect_id": "effect-x",
            "run_id": "run-x", "tool_id": "tool-x", "action_type": "WRITE",
            "target_ref": "target-x", "payload_integrity_hash": "a" * 64,
            "idempotency_key": "effect-key-x", "grant_id": "grant-x",
            "execution_state": "DECLARED", "effect_outcome": "UNDETERMINED",
            "reconciliation_status": "NOT_REQUIRED",
        }
        row = dict(effect, task_id="task-x", approval_ref=None, external_receipt_ref=None,
                   effect_json=json.dumps(effect, sort_keys=True))
        unresolved = _ClosureProjectionStore(self.fixture.store._validate, effects=(row,))
        self.assertEqual(_unresolved_effect_count(unresolved, "task-x"), 1)

        effect["execution_state"] = "FINISHED"
        effect["effect_outcome"] = "COMMITTED"
        effect["reconciliation_status"] = "RESOLVED"
        row = dict(effect, task_id="task-x", approval_ref=None, external_receipt_ref=None,
                   effect_json=json.dumps(effect, sort_keys=True))
        resolved = _ClosureProjectionStore(self.fixture.store._validate, effects=(row,))
        self.assertEqual(_unresolved_effect_count(resolved, "task-x"), 0)

    def test_unresolved_effect_projection_blocks_finish_before_binding(self):
        start, command = self.start_task("effect")
        plan = self.finish_plan(start, command)
        before = self.counts()
        with mock.patch("adapters.client.task_finish._unresolved_effect_count", return_value=1):
            with self.assertRaises(DailyTaskFinishError) as caught:
                self.finish(plan)
        self.assertEqual(caught.exception.reason_code, "DAILY_TASK_EFFECTS_UNRESOLVED")
        self.assertEqual(self.counts(), before)

    def test_exact_request_replay_after_binding_crash_skips_second_confirmation(self):
        start, command = self.start_task("partial")
        plan = self.finish_plan(start, command)
        confirmations = []
        original = self.fixture.authority.record_classification_assertion

        def crash_after_binding(*_args, **_kwargs):
            raise RuntimeError("injected crash")

        with mock.patch.object(self.fixture.authority, "record_classification_assertion", side_effect=crash_after_binding):
            with self.assertRaises(DailyTaskFinishError):
                self.finish(plan, lambda phrase, summary: confirmations.append((phrase, summary)) or True)
        with self.fixture.store._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM command_ledger WHERE command_id=?",
                                          (command + ":request",)).fetchone()[0], 1)
        result = self.finish(plan, lambda *_: self.fail("exact request replay must not reconfirm"))
        self.assertEqual(result["status"], "DAILY_TASK_FINISHED")
        self.assertEqual(len(confirmations), 1)
        self.assertNotEqual(original, None)

    def test_recovery_after_each_succeeded_durable_boundary(self):
        cases = ("class-verifying", "verifying", "class-terminal", "terminal", "revoke")
        for index, crash_after in enumerate(cases):
            with self.subTest(boundary=crash_after):
                start, command = self.start_task("boundary-" + str(index))
                plan = self.finish_plan(start, command)
                confirmations = []
                if crash_after.startswith("class-"):
                    target = crash_after
                    original = self.fixture.authority.record_classification_assertion

                    def crash_once(assertion, **kwargs):
                        result = original(assertion, **kwargs)
                        if kwargs["command_id"].endswith(target):
                            raise RuntimeError("response lost after classification")
                        return result

                    patcher = mock.patch.object(self.fixture.authority, "record_classification_assertion", side_effect=crash_once)
                elif crash_after in {"verifying", "terminal"}:
                    target = ":" + crash_after
                    original = self.fixture.trace.transition_run

                    def crash_once(**kwargs):
                        result = original(**kwargs)
                        if kwargs["command_id"].endswith(target):
                            raise RuntimeError("response lost after transition")
                        return result

                    patcher = mock.patch.object(self.fixture.trace, "transition_run", side_effect=crash_once)
                else:
                    original = self.fixture.authority.revoke_grant

                    def crash_once(grant_id, command_id):
                        original(grant_id, command_id)
                        raise RuntimeError("response lost after revoke")

                    patcher = mock.patch.object(self.fixture.authority, "revoke_grant", side_effect=crash_once)
                with patcher:
                    with self.assertRaises(DailyTaskFinishError):
                        self.finish(plan, lambda phrase, summary: confirmations.append(phrase) or True)
                result = self.finish(plan, lambda *_: self.fail("bound exact retry must not reconfirm"))
                self.assertEqual(result["status"], "DAILY_TASK_FINISHED")
                self.assertEqual(len(confirmations), 1)
                with self.fixture.store._connection() as conn:
                    rows = conn.execute(
                        "SELECT event_id FROM trace_events WHERE run_id=? AND event_type='nexus.run.transitioned'",
                        (start["root"]["root_run_id"],),
                    ).fetchall()
                event_ids = [row["event_id"] for row in rows]
                self.assertEqual(len(event_ids), len(set(event_ids)))

    def test_crash_before_request_binding_requires_confirmation_again(self):
        start, command = self.start_task("before-bind")
        plan = self.finish_plan(start, command)
        confirmations = []
        with mock.patch.object(self.fixture.store, "bind_command_request", side_effect=RuntimeError("before bind")):
            with self.assertRaises(DailyTaskFinishError):
                self.finish(plan, lambda phrase, summary: confirmations.append(phrase) or True)
        with self.fixture.store._connection() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM command_ledger WHERE command_id=?",
                                           (command + ":request",)).fetchone())
        self.finish(plan, lambda phrase, summary: confirmations.append(phrase) or True)
        self.assertEqual(confirmations, ["FINISH task-before-bind SUCCEEDED"] * 2)

    def test_changed_outcome_or_classification_conflicts_after_request_binding(self):
        start, command = self.start_task("changed")
        plan = self.finish_plan(start, command)
        with mock.patch.object(self.fixture.authority, "record_classification_assertion", side_effect=RuntimeError("crash")):
            with self.assertRaises(DailyTaskFinishError):
                self.finish(plan)
        changed = copy.deepcopy(plan)
        changed["classifications"]["terminal_event"]["reason"] = "Different classification semantic request."
        with self.assertRaises(DailyTaskFinishError) as caught:
            self.finish(changed)
        self.assertEqual(caught.exception.reason_code, "COMMAND_CONFLICT")

        failed = self.finish_plan(start, command, "FAILED")
        with self.assertRaises(DailyTaskFinishError) as caught:
            self.finish(failed)
        self.assertEqual(caught.exception.reason_code, "COMMAND_CONFLICT")

    def test_exact_completed_replay_after_expiry_and_revocation_is_historical(self):
        start, command = self.start_task("historical")
        plan = self.finish_plan(start, command)
        self.finish(plan)
        confirmations = []
        with mock.patch("kernel.authority.service._now", return_value=datetime.now(timezone.utc) + timedelta(days=3)):
            result = self.finish(plan, lambda *args: confirmations.append(args) or self.fail("no confirm"))
        self.assertEqual(result["grant_status"], "REVOKED")
        self.assertEqual(confirmations, [])

    def test_bound_partial_finish_expiry_stops_but_terminal_commit_may_revoke(self):
        start, command = self.start_task("expires-before-terminal")
        plan = self.finish_plan(start, command)
        with mock.patch.object(self.fixture.authority, "record_classification_assertion", side_effect=RuntimeError("crash after bind")):
            with self.assertRaises(DailyTaskFinishError):
                self.finish(plan)
        before = self.counts()
        with mock.patch("kernel.authority.service._now", return_value=datetime.now(timezone.utc) + timedelta(days=3)):
            with self.assertRaises(DailyTaskFinishError) as caught:
                self.finish(plan)
        self.assertEqual(caught.exception.reason_code, "DAILY_TASK_FINISH_AUTHORITY_UNAVAILABLE")
        self.assertEqual(self.counts(), before)

        start, command = self.start_task("expires-after-terminal")
        plan = self.finish_plan(start, command)
        original = self.fixture.trace.transition_run

        def crash_after_terminal(**kwargs):
            result = original(**kwargs)
            if kwargs["command_id"].endswith(":terminal"):
                raise RuntimeError("crash after terminal")
            return result

        with mock.patch.object(self.fixture.trace, "transition_run", side_effect=crash_after_terminal):
            with self.assertRaises(DailyTaskFinishError):
                self.finish(plan)
        with mock.patch("kernel.authority.service._now", return_value=datetime.now(timezone.utc) + timedelta(days=3)):
            result = self.finish(plan, lambda *_: self.fail("terminal recovery must not reconfirm"))
        self.assertEqual(result["grant_status"], "REVOKED")

    def test_foreign_terminal_transition_is_not_claimed_by_bound_finish(self):
        start = self.fixture.plan("foreign-terminal")
        finish_command = "finish-command-foreign-terminal"
        foreign_ref = "evt-foreign-terminal"
        start["grant"]["additional_resource_scope"] = sorted(
            set(start["grant"]["additional_resource_scope"] + [
                "evt-" + finish_command + ":verifying", "evt-" + finish_command + ":terminal", foreign_ref,
            ])
        )
        self.fixture.start(start)
        plan = self.finish_plan(start, finish_command)
        with mock.patch.object(self.fixture.authority, "record_classification_assertion", side_effect=RuntimeError("crash after bind")):
            with self.assertRaises(DailyTaskFinishError):
                self.finish(plan)

        assertion = {
            "schema_id": "nexus.classification_assertion", "schema_version": 1,
            "assertion_id": "foreign-terminal-class", "subject_type": "TRACE_EVENT",
            "subject_ref": foreign_ref, "sensitivity_level": "PUBLIC", "handling_tags": [],
            "policy_version": "1", "reason": "Independent fixture transition.", "actor_id": "runtime-service",
        }
        self.fixture.authority.record_classification_assertion(
            assertion, grant_id=start["grant"]["grant_id"], task_id=start["root"]["task_id"],
            audience="nexus-runtime", command_id="foreign-terminal-classify",
        )
        self.fixture.trace.transition_run(
            command_id="foreign-terminal", run_id=start["root"]["root_run_id"],
            expected_state="RUNNING", next_state="CANCELLED",
            classification_assertion_ref=assertion["assertion_id"],
        )
        with self.assertRaises(DailyTaskFinishError) as caught:
            self.finish(plan, lambda *_: self.fail("bound retry must not reconfirm"))
        self.assertEqual(caught.exception.reason_code, "DAILY_TASK_FINISH_STATE_CONFLICT")

    def test_cli_non_tty_rejects_before_writer_composition_and_has_no_bypass_flag(self):
        from adapters.client.__main__ import main

        start, command = self.start_task("cli")
        plan_path = self.fixture.base / "finish-plan.json"
        plan_path.write_text(json.dumps(self.finish_plan(start, command)), encoding="utf-8")
        argv = ["--data-root", str(self.fixture.root), "--policy", str(self.fixture.policy_path),
                "--independent-purge-journal", str(self.fixture.journal), "task", "finish", "--plan", str(plan_path)]
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch("adapters.client.__main__._runtime", side_effect=AssertionError("writer composition called")), \
             mock.patch("adapters.client.__main__.sys.stdin", io.StringIO("")), \
             mock.patch("adapters.client.__main__.sys.stdout", stdout), \
             mock.patch("adapters.client.__main__.sys.stderr", stderr):
            code = main(argv)
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stderr.getvalue())["reason"], "INTERACTIVE_TTY_REQUIRED")
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main(argv + ["--yes"])

    def test_cli_finish_wires_existing_root_and_emits_sanitized_result(self):
        from adapters.client.__main__ import main

        start, command = self.start_task("cli-success")
        plan_path = self.fixture.base / "finish-plan-cli.json"
        plan_path.write_text(json.dumps(self.finish_plan(start, command)), encoding="utf-8")
        argv = ["--data-root", str(self.fixture.root), "--policy", str(self.fixture.policy_path),
                "--independent-purge-journal", str(self.fixture.journal), "task", "finish", "--plan", str(plan_path)]
        stdin, stdout, stderr = _TTY("FINISH task-cli-success SUCCEEDED\n"), _TTY(), io.StringIO()
        components = (self.fixture.store, self.fixture.authority, self.fixture.budget,
                      self.fixture.trace, self.fixture.runtime)
        with mock.patch("adapters.client.__main__._runtime", return_value=components), \
             mock.patch("adapters.client.__main__.sys.stdin", stdin), \
             mock.patch("adapters.client.__main__.sys.stdout", stdout), \
             mock.patch("adapters.client.__main__.sys.stderr", stderr):
            code = main(argv)
        output_text = stdout.getvalue()
        output = json.loads(output_text[output_text.rfind("{\n"):])
        self.assertEqual(code, 0)
        self.assertEqual(output["status"], "DAILY_TASK_FINISHED")
        self.assertNotIn("PRIVATE_INPUT_MARKER", output_text)
        self.assertNotIn(str(self.fixture.base), output_text)

    def test_strict_plan_rejects_duplicate_keys_unknown_fields_and_nonfinite_values(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "plan.json"
            for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'[]'):
                path.write_bytes(raw)
                with self.assertRaises(DailyTaskFinishError):
                    read_task_finish_plan(path)
        start, command = self.start_task("strict")
        plan = self.finish_plan(start, command)
        plan["surprise"] = True
        before = self.counts()
        with self.assertRaises(DailyTaskFinishError) as caught:
            self.finish(plan)
        self.assertEqual(caught.exception.reason_code, "DAILY_TASK_FINISH_PLAN_INVALID")
        self.assertEqual(self.counts(), before)


if __name__ == "__main__":
    unittest.main()
