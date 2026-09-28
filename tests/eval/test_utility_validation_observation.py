from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts.eval.run_utility_validation_v1 import (
    CodexCliController,
    SANITIZED_ARGV,
    SANITIZED_ARGV_SHA256,
    parse_codex_jsonl,
)
from scripts.eval.utility_validation_observation import (
    ObservationLedgerError,
    UtilityObservationLedger,
    canonical_json,
    sha256_bytes,
    unavailable_host_usage,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 64
COMMIT = "b" * 40
THREAD = "fixture-thread-0001"
OUTPUT = "Acceptance output marker that must not be persisted"


class Tick:
    def __init__(self):
        self.value = 1_000_000_000

    def __call__(self):
        self.value += 1_000_000_000
        return self.value


def host_jsonl(*, thread_id=THREAD, output=OUTPUT, usage=None, tool=False):
    events = [
        {"type": "thread.started", "thread_id": thread_id},
        {"type": "item.completed", "item": {"id": "message-1", "type": "agent_message", "text": output}},
    ]
    if tool:
        events.insert(1, {"type": "item.started", "item": {"id": "tool-1", "type": "command_execution", "command": "private args excluded"}})
        events.insert(2, {"type": "item.completed", "item": {"id": "tool-1", "type": "command_execution", "command": "private args excluded"}})
    completed = {"type": "turn.completed"}
    if usage is not None:
        completed["usage"] = usage
    events.append(completed)
    return b"".join(json.dumps(event, separators=(",", ":")).encode("utf-8") + b"\n" for event in events)


class UtilityObservationLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nexus-utility-ledger-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.ledger = UtilityObservationLedger(
            self.base / "private" / "observations.jsonl",
            repository_root=REPO_ROOT,
            clock=lambda: "2026-09-28T00:00:00.000000Z",
            monotonic_ns=Tick(),
        )
        self.task_input = b"Frozen neutral task input\n"

    def _open(self, trial_id="trial-a", pair_id="pair-1", condition="HOST_NATIVE_BYPASS", **overrides):
        values = {
            "study_id": "utility-v1", "pair_id": pair_id, "trial_id": trial_id,
            "assigned_condition": condition,
            "task_card_sha256": SHA, "task_variant_sha256": "c" * 64,
            "task_payload_sha256": sha256_bytes(self.task_input),
            "starting_commit": COMMIT,
            "environment_snapshot_ref": "env:sha256", "environment_snapshot_sha256": "d" * 64,
            "acceptance_rubric_ref": "rubric:shared-v1", "acceptance_rubric_sha256": "e" * 64,
        }
        values.update(overrides)
        return self.ledger.open_trial(**values)

    def _accept_and_complete(self, session, *, verdict="PASS", retry_count=0):
        session.record_acceptance(
            rubric_ref="rubric:shared-v1", rubric_sha256="e" * 64,
            evaluator_kind="DETERMINISTIC", evaluated_output_sha256="f" * 64, verdict=verdict,
            evidence_refs=["evidence:sha256"], evidence_sha256=["f" * 64],
            first_pass=(retry_count == 0), retry_count=retry_count,
            inconclusive_reason="ACCEPTANCE_INCOMPLETE" if verdict == "INCONCLUSIVE" else None,
        )
        return session.complete()

    def test_valid_a_and_b_share_neutral_identity_without_fake_a_run(self):
        a = self._open(trial_id="trial-a", condition="HOST_NATIVE_BYPASS")
        b = self._open(
            trial_id="trial-b", condition="NEXUS_ACTIVE", nexus_task_id="nexus-task-1",
            nexus_run_id="nexus-run-1",
        )
        self.assertEqual((a.study_id, a.pair_id), (b.study_id, b.pair_id))
        opened = self.ledger.trial_events("utility-v1", "trial-a")[0]["payload"]
        self.assertIsNone(opened["nexus_task_id"])
        self.assertIsNone(opened["nexus_run_id"])
        invocation = "invocation-b"
        b.prepare_intervention(
            controller_invocation_id=invocation, preparation_status="PREPARED_NOT_SUBMITTED",
            boundary="CODEX_CLI_INPUT", composition_version="UTILITY_INTERVENTION_ENVELOPE_V1",
            task_payload_sha256=sha256_bytes(self.task_input), context_payload_sha256=SHA,
            final_cli_input_sha256=SHA, final_cli_input_bytes=100,
            context_pack_refs=["context-pack-1"], context_pack_byte_size=100,
            skill_payload_sha256=None, skill_resolution_ref=None, skill_resolution_status=None,
            skill_resolution=None, skill_candidate_count=None, selected_skill_ref=None,
            host_skill_availability=None, host_inventory_provenance=None,
            skill_instruction_load_status=None, skill_instruction_byte_size=None,
            skill_selection_latency_ms=None, skill_instruction_object_ref=None,
            nexus_task_id="nexus-task-1", nexus_run_id="nexus-run-1",
            provenance={"intervention_payload": "DERIVED"},
        )
        self.assertEqual(self.ledger.trial_events("utility-v1", "trial-b")[-1]["payload"]["controller_invocation_id"], invocation)

    def test_a_cannot_carry_nexus_refs_and_duplicate_trial_or_pair_condition_fails(self):
        with self.assertRaisesRegex(ObservationLedgerError, "LEDGER_A_CONDITION_MUST_NOT_HAVE_NEXUS_RUN"):
            self._open(nexus_task_id="fake-task", nexus_run_id="fake-run")
        self._open()
        with self.assertRaisesRegex(ObservationLedgerError, "LEDGER_DUPLICATE_TRIAL_ID"):
            self._open()
        with self.assertRaisesRegex(ObservationLedgerError, "LEDGER_DUPLICATE_PAIR_CONDITION"):
            self._open(trial_id="trial-a2")

    def test_illegal_transition_and_partial_sequence_are_preserved(self):
        session = self._open()
        with self.assertRaisesRegex(ObservationLedgerError, "LEDGER_ILLEGAL_EVENT_TRANSITION"):
            session.record_acceptance(
                rubric_ref="rubric:shared-v1", rubric_sha256="e" * 64,
                evaluator_kind="DETERMINISTIC", evaluated_output_sha256="f" * 64,
                verdict="PASS", evidence_refs=[], evidence_sha256=[],
                first_pass=True, retry_count=0, inconclusive_reason=None,
            )
        before = self.ledger.path.read_bytes()
        reopened = UtilityObservationLedger(self.ledger.path, repository_root=REPO_ROOT)
        self.assertEqual(reopened.events()[0]["event_type"], "TRIAL_OPENED")
        self.assertEqual(self.ledger.path.read_bytes(), before)
        self.assertEqual(len(reopened.trial_events("utility-v1", "trial-a")), 1)

    def test_hash_chain_detects_mutation_and_append_never_rewrites_prefix(self):
        self._open()
        first = self.ledger.path.read_bytes()
        session = self.ledger.trial_events("utility-v1", "trial-a")[0]
        self.ledger.append_event(
            study_id="utility-v1", pair_id="pair-1", trial_id="trial-a",
            event_type="TRIAL_INCONCLUSIVE", payload={
                "reason_code": "CONTROLLER_FAILURE", "trial_wall_elapsed_ms": 7,
                "host_process_elapsed_ms": None, "evidence_refs": [],
            },
        )
        after = self.ledger.path.read_bytes()
        self.assertTrue(after.startswith(first))
        self.assertEqual(session["event_type"], "TRIAL_OPENED")
        rows = after.splitlines()
        changed = json.loads(rows[0])
        changed["payload"]["task_card_sha256"] = "0" * 64
        rows[0] = canonical_json(changed)
        self.ledger.path.write_bytes(b"\n".join(rows) + b"\n")
        with self.assertRaisesRegex(ObservationLedgerError, "LEDGER_EVENT_HASH_INVALID"):
            self.ledger.events()

    def test_no_prompt_body_absolute_path_or_private_capture_is_persisted(self):
        session = self._open()
        with self.assertRaisesRegex(ObservationLedgerError, "LEDGER_PRIVATE_CONTENT_FIELD_FORBIDDEN"):
            session.ledger.append_event(
                study_id=session.study_id, pair_id=session.pair_id, trial_id=session.trial_id,
                event_type="TRIAL_INCONCLUSIVE", payload={
                    "reason_code": "CONTROLLER_FAILURE", "trial_wall_elapsed_ms": 1,
                    "host_process_elapsed_ms": None, "evidence_refs": [], "raw_prompt": "sanitized fixture only",
                },
            )
        with self.assertRaisesRegex(ObservationLedgerError, "LEDGER_PRIVATE_ABSOLUTE_PATH_FORBIDDEN"):
            session.ledger.append_event(
                study_id=session.study_id, pair_id=session.pair_id, trial_id=session.trial_id,
                event_type="TRIAL_INCONCLUSIVE", payload={
                    "reason_code": "CONTROLLER_FAILURE", "trial_wall_elapsed_ms": 1,
                    "host_process_elapsed_ms": None,
                    "evidence_refs": [str(self.base / "private" / "capture")],
                },
            )
        self.assertNotIn(OUTPUT.encode(), self.ledger.path.read_bytes())
        with self.assertRaisesRegex(ObservationLedgerError, "LEDGER_MUST_BE_REPO_EXTERNAL"):
            UtilityObservationLedger(REPO_ROOT / "inside-ledger.jsonl", repository_root=REPO_ROOT)

    def test_invalidation_and_unknown_values_are_explicit(self):
        session = self._open()
        session.ledger.append_event(
            study_id=session.study_id, pair_id=session.pair_id, trial_id=session.trial_id,
            event_type="TRIAL_INVALIDATED", payload={
                "reason_code": "PROVENANCE_AMBIGUOUS", "trial_wall_elapsed_ms": 1,
                "host_process_elapsed_ms": None, "evidence_refs": [],
            },
        )
        self.assertEqual(self.ledger.trial_events("utility-v1", "trial-a")[-1]["payload"]["reason_code"], "PROVENANCE_AMBIGUOUS")
        self.assertEqual(unavailable_host_usage()["input_tokens"], {"value": None, "provenance": "UNAVAILABLE"})


class UtilityControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nexus-utility-controller-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = REPO_ROOT
        self.tick = Tick()
        self.ledger = UtilityObservationLedger(self.root / "ledger.jsonl", repository_root=self.repo,
                                               clock=lambda: "2026-09-28T00:00:00Z",
                                               monotonic_ns=self.tick)
        self.task_input = b"Frozen task, no Nexus marker.\n"
        self.session = self.ledger.open_trial(
            study_id="study-1", pair_id="pair-1", trial_id="trial-a",
            assigned_condition="HOST_NATIVE_BYPASS", task_card_sha256=SHA,
            task_variant_sha256="c" * 64, task_payload_sha256=sha256_bytes(self.task_input),
            starting_commit=COMMIT, environment_snapshot_ref=None,
            environment_snapshot_sha256=None, acceptance_rubric_ref="rubric:v1",
            acceptance_rubric_sha256="d" * 64,
        )

    def test_canonical_argv_stdin_direct_execution_and_exact_provenance(self):
        expected_argv = (
            "codex", "exec", "--ephemeral", "--json", "--color", "never",
            "--sandbox", "read-only", "--skip-git-repo-check", "-",
        )
        self.assertEqual(SANITIZED_ARGV, expected_argv)
        self.assertNotIn("--ask-for-approval", SANITIZED_ARGV)
        self.assertEqual(SANITIZED_ARGV_SHA256, sha256_bytes(canonical_json(list(expected_argv))))
        captured = {}

        def runner(argv, **kwargs):
            captured["argv"] = argv
            captured.update(kwargs)
            self.assertEqual(list(argv), list(SANITIZED_ARGV))
            self.assertFalse(kwargs["shell"])
            self.assertEqual(kwargs["input"], self.task_input)
            self.assertEqual(list(Path(kwargs["cwd"]).iterdir()), [])
            self.assertFalse(Path(kwargs["cwd"]).is_relative_to(self.repo))
            return SimpleNamespace(returncode=0, stdout=host_jsonl(usage={
                "input_tokens": 41, "cached_input_tokens": 9, "output_tokens": 5,
                "reasoning_output_tokens": 2,
            }), stderr=b"")

        controller = CodexCliController(self.ledger, repository_root=self.repo,
                                        temp_root=self.root, subprocess_runner=runner,
                                        monotonic_ns=self.tick)
        result = controller.invoke(self.session, self.task_input, controller_invocation_id="invocation-1")
        self.assertEqual(result["sanitized_argv_sha256"], SANITIZED_ARGV_SHA256)
        self.assertEqual(result["input_sha256"], sha256_bytes(self.task_input))
        self.assertEqual(result["thread_id"], THREAD)
        self.assertTrue(result["turn_completed"])
        self.assertEqual(result["usage"]["input_tokens"], {"value": 41, "provenance": "HOST_DECLARED"})
        self.assertIsNone(result["usage"]["cache_write_input_tokens"]["value"])
        self.assertEqual(captured["input"], self.task_input)
        persisted = self.ledger.trial_events("study-1", "trial-a")
        started = next(row for row in persisted if row["event_type"] == "HOST_INVOCATION_STARTED")
        self.assertEqual(started["payload"]["sanitized_argv"], list(SANITIZED_ARGV))
        self.assertEqual(started["payload"]["sanitized_argv_sha256"], SANITIZED_ARGV_SHA256)
        self.assertEqual(started["payload"]["input_sha256"], sha256_bytes(self.task_input))
        self.assertEqual(persisted[-1]["event_type"], "HOST_INVOCATION_COMPLETED")
        self.assertEqual(persisted[-1]["payload"]["input_submission_status"], "CONTROLLER_SUBMITTED_TO_CODEX_CLI")
        self.assertEqual(persisted[-1]["payload"]["submitted_input_sha256"], sha256_bytes(self.task_input))
        self.assertEqual(persisted[-1]["payload"]["received_intervention"], "NONE")
        self.session.record_acceptance(
            rubric_ref="rubric:v1", rubric_sha256="d" * 64,
            evaluator_kind="DETERMINISTIC", evaluated_output_sha256=result["agent_output_sha256"],
            verdict="PASS", evidence_refs=[], evidence_sha256=[],
            first_pass=True, retry_count=0, inconclusive_reason=None,
        )
        terminal = self.session.complete()
        self.assertGreaterEqual(terminal["payload"]["trial_wall_elapsed_ms"], result["host_process_elapsed_ms"])
        self.assertNotIn(OUTPUT, self.ledger.path.read_text(encoding="utf-8"))

    def test_blinded_acceptance_receives_no_condition_and_is_bound_to_output_hash(self):
        from scripts.eval.run_utility_validation_v1 import evaluate_blind
        captured = {}

        def evaluator(**kwargs):
            captured.update(kwargs)
            return {
                "verdict": "PASS", "evidence_refs": ["shared-check:result"],
                "evidence_sha256": ["f" * 64], "inconclusive_reason": None,
            }

        output_bytes = OUTPUT.encode("utf-8")
        accepted = evaluate_blind(
            evaluator, output_bytes=output_bytes, rubric_ref="rubric:v1",
            rubric_sha256="d" * 64, retry_count=0,
        )
        self.assertEqual(set(captured), {"output_bytes", "rubric_ref", "rubric_sha256"})
        self.assertNotIn("assigned_condition", captured)
        self.assertEqual(accepted["evaluated_output_sha256"], sha256_bytes(output_bytes))
        self.assertTrue(accepted["first_pass"])

    def test_tool_events_are_summarized_without_arguments(self):
        parsed = parse_codex_jsonl(host_jsonl(tool=True))
        self.assertEqual(parsed.event_parse_status, "COMPLETE")
        self.assertEqual(parsed.tool_activity, ({"event_type": "command_execution", "count": 1},))
        self.assertNotIn("private args excluded", json.dumps(parsed.__dict__))

    def test_missing_usage_malformed_unknown_and_missing_thread_stay_unknown(self):
        missing = parse_codex_jsonl(host_jsonl(usage=None))
        self.assertIsNone(missing.usage["input_tokens"]["value"])
        self.assertEqual(missing.usage["input_tokens"]["provenance"], "UNAVAILABLE")
        malformed = parse_codex_jsonl(b"{broken\n" + host_jsonl())
        self.assertEqual(malformed.event_parse_status, "INCOMPLETE")
        self.assertEqual(malformed.malformed_line_count, 1)
        unknown = parse_codex_jsonl(b'{"type":"future.private_event","opaque_marker":"fixture-only"}\n' + host_jsonl())
        self.assertEqual(unknown.event_parse_status, "INCOMPLETE")
        self.assertEqual(unknown.unknown_event_count, 1)
        no_thread = parse_codex_jsonl(b'{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}\n{"type":"turn.completed"}\n')
        self.assertFalse(no_thread.thread_started)
        self.assertEqual(no_thread.event_parse_status, "INCOMPLETE")
        duplicate_thread = parse_codex_jsonl(host_jsonl() + b'{"type":"thread.started","thread_id":"' + THREAD.encode() + b'"}\n')
        self.assertFalse(duplicate_thread.thread_started)
        self.assertEqual(duplicate_thread.event_parse_status, "INCOMPLETE")

    def test_nonzero_exit_timeout_partial_output_and_no_retry(self):
        called = []

        def failed_runner(*args, **kwargs):
            called.append(1)
            return SimpleNamespace(returncode=17, stdout=host_jsonl(), stderr=b"do not infer from this text")

        ledger = self.ledger
        controller = CodexCliController(ledger, repository_root=self.repo, temp_root=self.root,
                                        subprocess_runner=failed_runner, monotonic_ns=self.tick)
        result = controller.invoke(self.session, self.task_input)
        self.assertEqual(result["exit_code"], 17)
        self.assertFalse(result["retry_performed"])
        self.assertEqual(len(called), 1)
        self.assertEqual(self.ledger.trial_events("study-1", "trial-a")[-1]["event_type"], "TRIAL_INCONCLUSIVE")
        persisted = self.ledger.path.read_text(encoding="utf-8")
        self.assertNotIn("do not infer from this text", persisted)
        self.assertNotIn("private args excluded", persisted)

        ledger2 = UtilityObservationLedger(self.root / "timeout-ledger.jsonl", repository_root=self.repo,
                                           clock=lambda: "2026-09-28T00:00:00Z", monotonic_ns=self.tick)
        session2 = ledger2.open_trial(
            study_id="study-1", pair_id="pair-2", trial_id="trial-timeout", assigned_condition="HOST_NATIVE_BYPASS",
            task_card_sha256=SHA, task_variant_sha256="c" * 64, task_payload_sha256=sha256_bytes(self.task_input),
            starting_commit=COMMIT, environment_snapshot_ref=None, environment_snapshot_sha256=None,
            acceptance_rubric_ref="rubric:v1", acceptance_rubric_sha256="d" * 64,
        )
        timeout_calls = []

        def timeout_runner(*args, **kwargs):
            timeout_calls.append(1)
            raise __import__("subprocess").TimeoutExpired(args[0], kwargs["timeout"], output=host_jsonl())

        timeout_controller = CodexCliController(ledger2, repository_root=self.repo, temp_root=self.root,
                                                subprocess_runner=timeout_runner, monotonic_ns=self.tick)
        timeout_result = timeout_controller.invoke(session2, self.task_input)
        self.assertTrue(timeout_result["timed_out"])
        self.assertEqual(len(timeout_calls), 1)
        self.assertEqual(ledger2.trial_events("study-1", "trial-timeout")[-1]["event_type"], "TRIAL_INCONCLUSIVE")

        ledger3 = UtilityObservationLedger(self.root / "missing-thread-ledger.jsonl", repository_root=self.repo,
                                           clock=lambda: "2026-09-28T00:00:00Z", monotonic_ns=self.tick)
        session3 = ledger3.open_trial(
            study_id="study-1", pair_id="pair-3", trial_id="trial-missing-thread",
            assigned_condition="HOST_NATIVE_BYPASS", task_card_sha256=SHA,
            task_variant_sha256="c" * 64, task_payload_sha256=sha256_bytes(self.task_input),
            starting_commit=COMMIT, environment_snapshot_ref=None, environment_snapshot_sha256=None,
            acceptance_rubric_ref="rubric:v1", acceptance_rubric_sha256="d" * 64,
        )
        missing_thread_stdout = (b'{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}\n'
                                 b'{"type":"turn.completed"}\n')
        missing_thread_controller = CodexCliController(
            ledger3, repository_root=self.repo, temp_root=self.root,
            subprocess_runner=lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0, stdout=missing_thread_stdout, stderr=b""),
            monotonic_ns=self.tick,
        )
        missing_thread_controller.invoke(session3, self.task_input, controller_invocation_id="missing-thread-call")
        terminal = ledger3.trial_events("study-1", "trial-missing-thread")[-1]
        self.assertEqual(terminal["event_type"], "TRIAL_INCONCLUSIVE")
        self.assertEqual(terminal["payload"]["reason_code"], "HOST_INVOCATION_ID_MISSING")

    def test_explicit_retry_is_linked_and_trial_wall_includes_preparation_and_all_host_time(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append(len(calls) + 1)
            return SimpleNamespace(
                returncode=0,
                stdout=host_jsonl(thread_id=f"fresh-thread-{len(calls)}"),
                stderr=b"",
            )

        controller = CodexCliController(
            self.ledger, repository_root=self.repo, temp_root=self.root,
            subprocess_runner=runner, monotonic_ns=self.tick,
        )
        first = controller.invoke(self.session, self.task_input, controller_invocation_id="invocation-first")
        # Represents real operator/evaluator work between attempts; it is on
        # the neutral trial clock even though no Host process is running.
        for _ in range(5):
            self.tick()
        second = controller.invoke(self.session, self.task_input, controller_invocation_id="invocation-retry")
        self.assertEqual(first["attempt_number"], 1)
        self.assertEqual(second["attempt_number"], 2)
        self.assertEqual(calls, [1, 2], "controller never retries by itself")
        self.session.record_acceptance(
            rubric_ref="rubric:v1", rubric_sha256="d" * 64,
            evaluator_kind="INDEPENDENT_REVIEWER", evaluated_output_sha256=second["agent_output_sha256"],
            verdict="PASS", evidence_refs=[], evidence_sha256=[],
            first_pass=False, retry_count=1, inconclusive_reason=None,
        )
        terminal = self.session.complete()
        self.assertEqual(terminal["payload"]["retry_count"], 1)
        self.assertFalse(terminal["payload"]["first_pass"])
        self.assertEqual(terminal["payload"]["host_process_elapsed_ms"],
                         first["host_process_elapsed_ms"] + second["host_process_elapsed_ms"])
        self.assertGreater(terminal["payload"]["trial_wall_elapsed_ms"],
                           terminal["payload"]["host_process_elapsed_ms"])

    def test_b_intervention_preparation_is_not_recorded_as_cli_submission(self):
        b = self.ledger.open_trial(
            study_id="study-1", pair_id="pair-prepared", trial_id="trial-b-prepared",
            assigned_condition="NEXUS_ACTIVE", task_card_sha256=SHA,
            task_variant_sha256="c" * 64, task_payload_sha256=sha256_bytes(self.task_input),
            starting_commit=COMMIT, environment_snapshot_ref=None,
            environment_snapshot_sha256=None, acceptance_rubric_ref="rubric:v1",
            acceptance_rubric_sha256="d" * 64, nexus_task_id="task-1", nexus_run_id="run-1",
        )
        b.prepare_intervention(
            controller_invocation_id="prepared-only",
            preparation_status="PREPARED_NOT_SUBMITTED", boundary="CODEX_CLI_INPUT",
            composition_version="UTILITY_INTERVENTION_ENVELOPE_V1",
            task_payload_sha256=sha256_bytes(self.task_input), final_cli_input_sha256=SHA,
            final_cli_input_bytes=100, context_payload_sha256=SHA,
            context_pack_refs=["context-pack-1"], context_pack_byte_size=90,
            skill_payload_sha256=None, skill_resolution_ref=None,
            skill_resolution_status=None, skill_resolution=None, skill_candidate_count=None,
            selected_skill_ref=None, host_skill_availability=None, host_inventory_provenance=None,
            skill_instruction_load_status=None, skill_instruction_byte_size=None,
            skill_selection_latency_ms=None,
            skill_instruction_object_ref=None, nexus_task_id="task-1", nexus_run_id="run-1",
            provenance={"intervention_payload": "DERIVED"},
        )
        events = self.ledger.trial_events("study-1", "trial-b-prepared")
        self.assertEqual(events[-1]["payload"]["preparation_status"], "PREPARED_NOT_SUBMITTED")
        self.assertFalse(any(event["event_type"] == "HOST_INVOCATION_STARTED" for event in events))
        with self.assertRaisesRegex(ObservationLedgerError, "LEDGER_TRIAL_COMPLETION_PREREQUISITES_MISSING"):
            b.complete()

    def test_controller_can_record_shared_blind_acceptance_without_persisting_output(self):
        controller = CodexCliController(
            self.ledger, repository_root=self.repo, temp_root=self.root,
            subprocess_runner=lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0, stdout=host_jsonl(), stderr=b""),
            monotonic_ns=self.tick,
        )
        evaluator_inputs = {}

        def evaluator(**kwargs):
            evaluator_inputs.update(kwargs)
            return {
                "verdict": "PASS", "evidence_refs": ["acceptance:objective-check"],
                "evidence_sha256": ["f" * 64], "inconclusive_reason": None,
            }

        result = controller.invoke(
            self.session, self.task_input, controller_invocation_id="invocation-accepted",
            acceptance_evaluator=evaluator, evaluator_kind="DETERMINISTIC",
        )
        self.assertEqual(set(evaluator_inputs), {"output_bytes", "rubric_ref", "rubric_sha256"})
        self.assertEqual(evaluator_inputs["output_bytes"], OUTPUT.encode("utf-8"))
        self.assertNotIn("assigned_condition", evaluator_inputs)
        events = self.ledger.trial_events("study-1", "trial-a")
        acceptance = next(item for item in events if item["event_type"] == "ACCEPTANCE_RECORDED")
        self.assertEqual(acceptance["payload"]["evaluated_output_sha256"], result["agent_output_sha256"])
        self.assertEqual(events[-1]["event_type"], "TRIAL_COMPLETED")
        self.assertEqual(result["trial_wall_elapsed_ms"], events[-1]["payload"]["trial_wall_elapsed_ms"])
        self.assertNotIn(OUTPUT.encode(), self.ledger.path.read_bytes())
