import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.eval.run_behavioral_phase5 import (
    ARGV_HASH_RULE,
    CODEX_EXECUTABLE_OBSERVATION,
    DIAGNOSTIC_SENTINEL,
    EXECUTION_HARNESS_VERSION,
    FROZEN_SANITIZED_ARGV,
    OFFICIAL_CODEX_EXEC_ARGS,
    PRIVATE_CAPTURE_LOCATION,
    _external_capture_dir,
    _argv_sha256,
    _host_command,
    _run_one,
    _serialize_persisted_result,
    score,
)


ROOT = Path(__file__).resolve().parents[2]
PACKETS = ROOT / "eval/academy/fixtures/behavioral-phase5-packets.json"
EVALUATOR = ROOT / "eval/academy/fixtures/behavioral-phase5-evaluator.json"
RESULT = ROOT / "eval/academy/results/behavioral-phase5.json"
CANDIDATES = ROOT / "eval/academy/results/bootstrap-academy-candidates.json"
SYNTHESIS = ROOT / "eval/academy/bootstrap-academy-synthesis.md"
RUNBOOK = ROOT / "eval/academy/behavioral-phase5-runbook.md"
PHASE5_DOC = ROOT / "eval/academy/behavioral-phase5.md"
OFFICIAL_SPEC = "PHASE5_OFFICIAL_HOST_INVOCATION_V1"


class BehavioralPhase5ProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packets = json.loads(PACKETS.read_text(encoding="utf-8"))
        cls.evaluator = json.loads(EVALUATOR.read_text(encoding="utf-8"))
        cls.result = json.loads(RESULT.read_text(encoding="utf-8"))

    def test_v2_incident_is_preserved_as_invalid_provenance_and_v3_has_no_formal_trials(self):
        digest = hashlib.sha256(PACKETS.read_bytes()).hexdigest()
        self.assertEqual(digest, self.result["fixture_sha256"])
        self.assertEqual(18, self.packets["packet_count"])
        self.assertEqual("PHASE5_PREREGISTRATION_V3", self.packets["protocol_version"])
        self.assertEqual("PHASE5_PREREGISTRATION_V3", self.result["protocol_version"])
        self.assertEqual("REPLACED BEFORE ANY FORMAL BEHAVIORAL TRIAL", self.result["protocol_history"]["PHASE5_PREREGISTRATION_V1"])
        self.assertIn("INVOCATION PROVENANCE CONFLICT", self.result["protocol_history"]["PHASE5_PREREGISTRATION_V2"])
        self.assertIn("HARDENED BEFORE FIRST ELIGIBLE FORMAL TRIAL", self.result["protocol_history"]["PHASE5_PREREGISTRATION_V3"])
        self.assertEqual(0, self.result["execution_count"])
        self.assertEqual(0, self.result["formal_trial_count"])
        self.assertEqual(1, self.result["attempted_count"])
        self.assertEqual([], self.result["formal_trials"])
        self.assertEqual("STOPPED_ON_INVOCATION_PROVENANCE_CONFLICT", self.result["matrix_status"])
        self.assertEqual("INVALID", self.result["execution_incidents"][0]["formal_behavioral_evidence"])
        self.assertFalse(self.result["execution_incidents"][0]["behavioral_inference_allowed"])
        self.assertEqual(0, self.result["eligible_formal_trial_count"])
        self.assertEqual(0, self.result["nexus_model_receipt_count"])
        self.assertFalse(self.result["manual_host_runs_required"])
        self.assertEqual("STOPPED_ON_INVOCATION_PROVENANCE_CONFLICT", self.result["execution_summary"]["matrix_status"])
        self.assertTrue(self.result["execution_summary"]["first_execution_only"])
        self.assertEqual(1, self.result["execution_summary"]["attempted_trial_count"])
        self.assertEqual(0, self.result["execution_summary"]["eligible_formal_trial_count"])
        self.assertEqual(0, self.result["execution_summary"]["context_completed_count"])
        self.assertEqual(0, self.result["execution_summary"]["presence_completed_count"])
        self.assertEqual(0, self.result["execution_summary"]["unique_thread_id_count"])
        self.assertEqual(0, self.result["execution_summary"]["tool_contaminated_count"])
        self.assertEqual(1, self.result["execution_summary"]["incomplete_or_pre_model_failure_count"])
        self.assertEqual("UNCHARACTERIZED", self.result["execution_summary"]["cross_session_memory_confound"])
        incident = self.result["execution_incidents"][0]
        self.assertEqual("A17", incident["attempted_behavioral_packet"])
        self.assertEqual("INVOCATION_PROVENANCE_CONFLICT", incident["classification"])
        self.assertEqual(0, incident["forensic_capture"]["stdout_bytes"])
        self.assertEqual("HOST_STATE_INITIALIZATION_OR_ACCESS_FAILURE", incident["forensic_capture"]["stderr_classification"])
        self.assertIn("does not establish the argv", incident["forensic_capture"]["secret_safe_summary"])
        self.assertEqual("HISTORICAL_CLI_OPTION_PROBE; NOT EVIDENCE OF THE V2 FORMAL A17 ARGV", self.result["historical_pre_model_cli_probe"]["scope"])
        self.assertNotIn("exact_argv", incident)
        self.assertNotIn("context_trials", self.result)
        self.assertEqual({}, self.result["presence_trials"])

    def test_official_invocation_and_fixed_order_are_frozen(self):
        expected_command = "codex exec --ephemeral --json --color never --sandbox read-only --skip-git-repo-check -"
        self.assertEqual(OFFICIAL_SPEC, self.packets["official_invocation_spec_id"])
        self.assertEqual(OFFICIAL_SPEC, self.result["official_invocation_spec_id"])
        self.assertEqual(expected_command, self.result["official_command"])
        order = self.packets["execution_order"]
        self.assertEqual(["A17", "E04", "V06", "F22", "B29", "H11", "T63", "D31", "R41", "G08", "K02", "J15", "U07", "L73", "M26", "S12", "N58", "W34"], order)
        self.assertEqual(18, len(order))
        self.assertEqual(18, len(set(order)))
        self.assertEqual(order, self.result["execution_order"])
        digest = hashlib.sha256(("\n".join(order) + "\n").encode()).hexdigest()
        self.assertEqual(digest, self.packets["execution_order_sha256"])
        self.assertEqual(digest, self.result["execution_order_sha256"])

    def test_semantic_exposure_order_is_monotonic_within_each_family(self):
        positions = {trial_id: index for index, trial_id in enumerate(self.packets["execution_order"])}
        conditions_by_family = {}
        for trial_id, mapping in self.evaluator["context_trials"].items():
            conditions_by_family.setdefault(mapping["family"], {})[mapping["condition"]] = positions[trial_id]
        self.assertEqual(4, len(conditions_by_family))
        for condition_positions in conditions_by_family.values():
            self.assertLess(condition_positions["C0_NONE"], condition_positions["C1_MINIMAL_RELEVANT"])
            self.assertLess(condition_positions["C1_MINIMAL_RELEVANT"], condition_positions["C2_BROAD_SAFE"])

        presence_by_task = {}
        for trial_id, mapping in self.evaluator["presence_trials"].items():
            presence_by_task.setdefault(mapping["task_type"], {})[mapping["presence"]] = positions[trial_id]
        self.assertEqual(2, len(presence_by_task))
        for presence_positions in presence_by_task.values():
            self.assertLess(presence_positions["P0_ABSENT"], presence_positions["P1_METADATA_ONLY"])
            self.assertLess(presence_positions["P1_METADATA_ONLY"], presence_positions["P2_FULL_INSTRUCTION"])

    def test_v3_changes_metadata_without_changing_packet_text_or_evaluator(self):
        rows = sorted(self.packets["context_packets"], key=lambda row: row["trial_id"])
        packet_text_digest = hashlib.sha256("\n".join(row["trial_id"] + "\0" + row["packet_text"] for row in rows).encode() + b"\n").hexdigest()
        self.assertEqual("66f3217dc376519668bc2e415904923e7d09dc7c1a3a0bef4a4b2f5ae6f3bdb6", packet_text_digest)
        self.assertEqual(packet_text_digest, self.result["model_visible_packet_text_sha256"])
        self.assertEqual("96e919d4798ebea839e6c58949c7fa1706d53806d3dc5041a71476b7a35ff25c", hashlib.sha256(EVALUATOR.read_bytes()).hexdigest())
        self.assertEqual("PHASE5_EXECUTION_HARNESS_V3", self.result["execution_harness_version"])
        self.assertTrue(self.result["formal_execution_authorized"])
        self.assertEqual("EXTERNAL_WINDOWS_POWERSHELL", self.result["formal_execution_controller"])
        self.assertEqual("EXTERNAL_REVIEW_ACCEPTED_PREREGISTRATION_V3_AND_EXTERNAL_HOST_PATH_DIAGNOSTIC", self.result["authorization_basis"])
        self.assertEqual("AUTHORIZED_NOT_STARTED", self.result["formal_matrix_status"])
        self.assertEqual(0, self.result["eligible_formal_behavioral_trials"])
        self.assertEqual("PHASE5_PREREGISTRATION_V2", self.result["execution_summary"]["protocol_version"])
        self.assertEqual("NOT STARTED", self.result["first_eligible_formal_matrix_execution_attempt"])

    def test_evaluator_mapping_covers_all_packets(self):
        ids = [row["trial_id"] for row in self.packets["context_packets"]]
        self.assertEqual(18, len(ids))
        self.assertEqual(18, len(set(ids)))
        self.assertEqual(set(ids), set(self.evaluator["context_trials"]) | set(self.evaluator["presence_trials"]))
        self.assertEqual(set(ids), set(self.packets["execution_order"]))

    def test_context_packets_are_blind_and_expected_answers_are_separate(self):
        ids = [row["trial_id"] for row in self.packets["context_packets"]]
        self.assertEqual(18, len(ids))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), set(self.evaluator["context_trials"]) | set(self.evaluator["presence_trials"]))
        self.assertEqual(12, len(self.evaluator["context_trials"]))
        self.assertEqual(6, len(self.evaluator["presence_trials"]))
        for row in self.packets["context_packets"]:
            self.assertNotIn("condition", row)
            self.assertNotIn("expected", row)
            self.assertNotIn("relevant", row)
            self.assertNotRegex(row["packet_text"], r"(?i)C0|C1|C2|minimal|broad|distractor|expected answer")

    def test_each_context_family_has_three_frozen_conditions(self):
        conditions = {}
        for trial in self.evaluator["context_trials"].values():
            conditions.setdefault(trial["family"], set()).add(trial["condition"])
        self.assertEqual(4, len(conditions))
        for labels in conditions.values():
            self.assertEqual({"C0_NONE", "C1_MINIMAL_RELEVANT", "C2_BROAD_SAFE"}, labels)

    def test_presence_pilot_has_synthetic_only_and_p3_is_not_run(self):
        ids = set(self.evaluator["presence_trials"])
        self.assertEqual({"V06", "R41", "S12", "T63", "U07", "W34"}, ids)
        self.assertFalse(any("P3" in trial["presence"] for trial in self.evaluator["presence_trials"].values()))
        self.assertIn("Glyph Shift", "\n".join(row["packet_text"] for row in self.packets["context_packets"]))
        self.assertEqual(0, self.result["nexus_model_receipt_count"])

    def test_a17_is_excluded_and_token_usage_is_host_reported_total(self):
        diagnostic = self.result["a17_diagnostic_not_counted"]
        self.assertEqual("BRIDGE_DIAGNOSTIC_NOT_COUNTED", diagnostic["status"])
        self.assertFalse(self.result["execution_incidents"][0]["attempted_packet_record"]["formal_trial_counted"])
        self.assertEqual("HOST_REPORTED_TOTAL_INPUT_TOKENS", diagnostic["usage_basis"])
        self.assertEqual({"input_tokens": 20488, "cached_input_tokens": 7936, "output_tokens": 21, "reasoning_output_tokens": 10}, diagnostic["usage"])
        self.assertEqual("HOST_REPORTED_TOTAL_INPUT_TOKENS", self.result["prior_worktree_liveness_diagnostic"]["usage_basis"])
        self.assertEqual({"input_tokens": 20447, "cached_input_tokens": 7936, "output_tokens": 23, "reasoning_output_tokens": 12}, self.result["prior_worktree_liveness_diagnostic"]["usage"])
        self.assertEqual("UNAVAILABLE / NOT DECOMPOSABLE FROM HOST TELEMETRY", diagnostic["ambient_host_input_composition"])
        self.assertEqual("UNAVAILABLE", diagnostic["dollar_cost"])
        self.assertEqual("UNAVAILABLE", self.result["tokens_and_cost"]["provider_cost"])
        self.assertEqual("HOST_REPORTED_TOTAL_TURN_TOKEN_TELEMETRY", self.result["tokens_and_cost"]["host_token_telemetry"])
        self.assertEqual("UNAVAILABLE", self.result["tokens_and_cost"]["provider_dollar_cost"])
        self.assertEqual("UNAVAILABLE", self.result["tokens_and_cost"]["host_model_identity"])
        self.assertEqual("UNAVAILABLE", self.result["tokens_and_cost"]["per_exposure_token_attribution"])
        self.assertEqual("UNAVAILABLE / NOT DECOMPOSABLE", self.result["tokens_and_cost"]["ambient_host_input_composition"])
        self.assertNotIn("skill_tokens", json.dumps(self.result).lower())
        self.assertNotIn("packet_tokens", json.dumps(self.result).lower())

    def test_gates_exposure_and_lifecycle_states_are_consistent(self):
        self.assertEqual("SUPPORTED_BY_REAL_HOST_PILOT", self.result["gates"]["G1_EXPLICIT_EXPERIMENT_PACKET_DELIVERY"])
        self.assertEqual("UNAVAILABLE", self.result["gates"]["G2_FULL_MODEL_VISIBLE_CONTEXT_PROOF"])
        self.assertEqual("NOT OBSERVED", self.result["gates"]["H1_SYNTHETIC_CAPABILITY_PRESENCE_BEHAVIOR"])
        self.assertEqual("NOT TESTED", self.result["gates"]["H2_REAL_SKILL_CAPABILITY_UTILITY"])
        self.assertEqual("UNAVAILABLE / NOT INDEPENDENTLY VERIFIED", self.result["gates"]["I_HOST_MEMORY_CONTAMINATION"])
        self.assertEqual("SUPPORTED_BY_REAL_HOST", self.result["gates"]["J1_HOST_TOTAL_TURN_TOKEN_TELEMETRY"])
        self.assertEqual("UNAVAILABLE", self.result["gates"]["J2_PROVIDER_DOLLAR_COST"])
        self.assertEqual("KNOWN", self.result["exposure_observability"]["explicit_packet_visibility"])
        self.assertEqual("HELD_CONSTANT_BUT_PARTIALLY_OBSERVABLE", self.result["exposure_observability"]["ambient_host_context"])
        self.assertEqual("UNAVAILABLE", self.result["exposure_observability"]["full_model_visible_context"])
        candidates = json.loads(CANDIDATES.read_text(encoding="utf-8"))
        candidate_gates = {item["gate"]: item["status"] for item in candidates["shadow_entry_gate_candidate"]}
        self.assertEqual("SUPPORTED_BY_REAL_HOST", candidate_gates["J1_HOST_TOTAL_TURN_TOKEN_TELEMETRY"])
        self.assertEqual("UNAVAILABLE", candidate_gates["J2_PROVIDER_DOLLAR_COST"])
        self.assertEqual("UNAVAILABLE", candidates["host_model_identity"])
        exposure_budget = next(item for item in candidates["candidates"] if item["candidate_id"] == "CAPABILITY_EXPOSURE_BUDGET")
        self.assertIn("Per-exposure token attribution", exposure_budget["evidence_missing"])
        self.assertIn("behavioral/cost outcome evidence", exposure_budget["evidence_missing"])
        synthesis_text = SYNTHESIS.read_text(encoding="utf-8")
        self.assertIn("J1. Host total-turn token telemetry | SUPPORTED BY REAL HOST", synthesis_text)
        self.assertIn("J2. Provider dollar cost | UNAVAILABLE", synthesis_text)
        self.assertNotIn("No reliable Host telemetry", synthesis_text)
        phases = {item["phase"]: item["status"] for item in candidates["phases"]}
        self.assertEqual("CLOSED / ACCEPTED", phases["4"])
        self.assertTrue(phases["5"].startswith("IN PROGRESS / EXTERNAL CONTROLLER AUTHORIZED"))
        self.assertEqual("NOT OBSERVED", candidate_gates["H1_SYNTHETIC_CAPABILITY_PRESENCE_BEHAVIOR"])
        self.assertEqual("NOT TESTED", candidate_gates["H2_REAL_SKILL_CAPABILITY_UTILITY"])
        self.assertEqual("NOT STARTED", candidates["capability_certification"]["status"])
        self.assertEqual("NOT STARTED", candidates["shadow"])
        self.assertEqual("NOT STARTED", candidates["production_qualification"])

    def test_docs_no_longer_claim_manual_execution_is_required_or_bridge_blocked(self):
        synthesis = SYNTHESIS.read_text(encoding="utf-8")
        runbook = RUNBOOK.read_text(encoding="utf-8")
        phase5_doc = PHASE5_DOC.read_text(encoding="utf-8")
        self.assertIn("automated_host_behavioral_bridge", self.result)
        self.assertIn("FEASIBLE", self.result["automated_host_behavioral_bridge"])
        self.assertNotIn("HOST_MANUAL_EXECUTION_REQUIRED", synthesis + runbook + phase5_doc)
        self.assertNotIn("MANUAL HOST RUNS REQUIRED", synthesis + runbook + phase5_doc)
        self.assertNotIn("manual fresh-chat runs are required", (synthesis + phase5_doc).lower())
        self.assertIn("CROSS_SESSION_MEMORY_CONFOUND` is `UNCHARACTERIZED", phase5_doc)
        self.assertIn("STOPPED_ON_INVOCATION_PROVENANCE_CONFLICT", phase5_doc)
        self.assertIn("V3 diagnostic", phase5_doc)
        self.assertIn("fallback artifact", runbook.lower())
        self.assertIn("ordinary Windows PowerShell", runbook)
        self.assertIn("not a Codex integrated/Agent shell", runbook)
        self.assertIn("current PowerShell process only", runbook)
        self.assertIn("--sandbox read-only", runbook)
        self.assertIn("python scripts/eval/run_behavioral_phase5.py --execute-host --timeout 180", runbook)
        self.assertNotIn("C:\\Users\\", runbook + phase5_doc + synthesis)

    def test_runner_serialization_omits_executable_and_capture_absolute_paths(self):
        executable = r"C:\Users\reviewer\AppData\Local\Programs\Codex\codex.exe"
        capture = r"C:\Users\reviewer\AppData\Local\Temp\nexus-academy-phase5-captures-123"
        serialized = _serialize_persisted_result({
            "codex_executable": executable,
            "raw_capture_location": capture,
            "raw_capture_stdout": "A17.stdout.jsonl",
            "raw_capture_stderr": "A17.stderr.txt",
        })
        self.assertNotIn(executable, serialized)
        self.assertNotIn(capture, serialized)
        self.assertNotIn("C:\\Users\\", serialized)
        self.assertNotIn("/Users/", serialized)
        self.assertNotIn("/home/", serialized)
        self.assertNotIn(str(Path.home()), serialized)
        self.assertIn(CODEX_EXECUTABLE_OBSERVATION, serialized)
        self.assertIn(PRIVATE_CAPTURE_LOCATION, serialized)
        self.assertIn("A17.stdout.jsonl", serialized)
        self.assertIn("A17.stderr.txt", serialized)

    def test_mocked_run_one_uses_frozen_argv_and_persists_provenance_before_outcome(self):
        codex = str(Path(tempfile.gettempdir()) / "fake-codex-executable")
        packet = "harmless unit-test packet"
        events = []
        stdout = "\n".join((
            json.dumps({"type": "thread.started", "thread_id": "test-thread"}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "OK"}}),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 2, "output_tokens": 1}}),
        ))

        def persist_before_run(provenance):
            events.append(("persist", provenance.copy()))

        def mocked_run(command, **kwargs):
            events.append(("subprocess", command.copy(), kwargs.copy()))
            self.assertEqual([codex, *OFFICIAL_CODEX_EXEC_ARGS], command)
            self.assertNotIn("--ask-for-approval", command)
            self.assertIs(kwargs["shell"], False)
            self.assertEqual(packet, kwargs["input"])
            cwd = Path(kwargs["cwd"])
            self.assertTrue(cwd.is_dir())
            self.assertEqual([], list(cwd.iterdir()))
            self.assertNotIn(ROOT.resolve(), cwd.parents)
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

        import subprocess
        with tempfile.TemporaryDirectory() as directory, patch("scripts.eval.run_behavioral_phase5.subprocess.run", side_effect=mocked_run):
            observed = _run_one(codex, "UNIT", packet, 10, Path(directory), set(), persist_before_run)

        self.assertEqual("persist", events[0][0])
        self.assertEqual("subprocess", events[1][0])
        self.assertEqual(list(FROZEN_SANITIZED_ARGV), events[0][1]["sanitized_argv"])
        self.assertEqual(_argv_sha256(FROZEN_SANITIZED_ARGV), events[0][1]["sanitized_argv_sha256"])
        self.assertEqual(ARGV_HASH_RULE, events[0][1]["sanitized_argv_hash_rule"])
        self.assertFalse(events[0][1]["subprocess_shell"])
        self.assertTrue(events[0][1]["argv_persisted_before_process_outcome"])
        self.assertNotIn(codex, json.dumps(observed))
        self.assertEqual("COMPLETED", observed["execution_status"])
        self.assertTrue(observed["formal_behavioral_trial_eligible"])

    def test_forbidden_argument_guard_fails_before_subprocess(self):
        with patch(
            "scripts.eval.run_behavioral_phase5.OFFICIAL_CODEX_EXEC_ARGS",
            (*OFFICIAL_CODEX_EXEC_ARGS, "--ask-for-approval", "never"),
        ):
            with self.assertRaisesRegex(RuntimeError, "Forbidden Host argument"):
                _host_command("codex")

    def test_argv_provenance_survives_pre_model_process_failure(self):
        codex = "codex-test"
        events = []

        def persist(provenance):
            events.append(("persist", provenance.copy()))

        def failed_run(command, **kwargs):
            events.append(("subprocess", command.copy(), kwargs.copy()))
            return subprocess.CompletedProcess(command, 2, stdout="", stderr="parser error")

        import subprocess
        with tempfile.TemporaryDirectory() as directory, patch("scripts.eval.run_behavioral_phase5.subprocess.run", side_effect=failed_run):
            observed = _run_one(codex, "UNIT-FAIL", "harmless packet", 10, Path(directory), set(), persist)

        self.assertEqual(["persist", "subprocess"], [event[0] for event in events])
        self.assertEqual(list(FROZEN_SANITIZED_ARGV), observed["sanitized_argv"])
        self.assertEqual(_argv_sha256(FROZEN_SANITIZED_ARGV), observed["sanitized_argv_sha256"])
        self.assertEqual(2, observed["exit_code"])
        self.assertEqual("PRE_MODEL_HOST_INVOCATION_FAILURE", observed["execution_status"])

    def test_diagnostic_metadata_is_non_behavioral_and_does_not_upgrade_gates(self):
        self.assertEqual("BLOCKED_BY_HOST_STATE_ACCESS", self.result["v3_host_execution_path"])
        diagnostic = next(item for item in self.result["host_diagnostics"] if item.get("kind") == "NON_BEHAVIORAL_HOST_LIVENESS_DIAGNOSTIC")
        self.assertEqual("NON_BEHAVIORAL_HOST_LIVENESS_DIAGNOSTIC", diagnostic["kind"])
        self.assertEqual(list(FROZEN_SANITIZED_ARGV), diagnostic["sanitized_argv"])
        self.assertEqual(_argv_sha256(FROZEN_SANITIZED_ARGV), diagnostic["sanitized_argv_sha256"])
        self.assertEqual(ARGV_HASH_RULE, diagnostic["sanitized_argv_hash_rule"])
        self.assertEqual(1, diagnostic["exit_code"])
        self.assertFalse(diagnostic["thread_started"])
        self.assertIsNone(diagnostic["thread_id"])
        self.assertFalse(diagnostic["turn_completed"])
        self.assertIsNone(diagnostic["agent_output"])
        self.assertEqual("UNAVAILABLE", diagnostic["host_usage"])
        self.assertEqual("UNAVAILABLE", diagnostic["usage_basis"])
        self.assertEqual("UNAVAILABLE", diagnostic["tool_call_count"])
        self.assertEqual("HOST_STATE_INITIALIZATION_OR_ACCESS_FAILURE", diagnostic["stderr_classification"])
        self.assertFalse(diagnostic["behavioral_trial_counted"])
        self.assertFalse(diagnostic["nexus_model_receipt_created"])
        self.assertFalse(diagnostic["subprocess_shell"])
        self.assertTrue(diagnostic["argv_persisted_before_process_outcome"])
        self.assertNotIn(str(Path.home()), json.dumps(diagnostic))
        self.assertEqual(0, self.result["formal_trial_count"])
        self.assertEqual(0, self.result["nexus_model_receipt_count"])
        self.assertEqual("SUPPORTED_BY_REAL_HOST_PILOT", self.result["gates"]["G1_EXPLICIT_EXPERIMENT_PACKET_DELIVERY"])
        self.assertEqual("UNAVAILABLE", self.result["gates"]["G2_FULL_MODEL_VISIBLE_CONTEXT_PROOF"])
        self.assertEqual("NOT OBSERVED", self.result["gates"]["H1_SYNTHETIC_CAPABILITY_PRESENCE_BEHAVIOR"])
        self.assertEqual("NOT TESTED", self.result["gates"]["H2_REAL_SKILL_CAPABILITY_UTILITY"])

    def test_external_shell_diagnostic_authorizes_controller_without_counting_trial(self):
        self.assertEqual("SUPPORTED_BY_EXTERNAL_SHELL_DIAGNOSTIC", self.result["nested_outer_sandbox_confound"].split(";")[0])
        self.assertEqual("BLOCKED_BY_HOST_STATE_ACCESS", self.result["codex_agent_nested_execution_path"])
        self.assertEqual("SUPPORTED", self.result["external_windows_powershell_execution_path"])
        diagnostic = next(item for item in self.result["host_diagnostics"] if item.get("kind") == "NON_BEHAVIORAL_EXTERNAL_HOST_DIAGNOSTIC")
        self.assertEqual("PHASE5_OFFICIAL_HOST_INVOCATION_V1", diagnostic["invocation_spec_id"])
        self.assertEqual(0, diagnostic["exit_code"])
        self.assertTrue(diagnostic["thread_started"])
        self.assertEqual("01a0e1ed-4206-7671-98ba-431aac9ef614", diagnostic["thread_id"])
        self.assertTrue(diagnostic["turn_completed"])
        self.assertEqual("P5_OUTER_HOST_OK", diagnostic["agent_output"])
        self.assertEqual(0, diagnostic["tool_call_count"])
        self.assertEqual("HOST_REPORTED_TOTAL_TURN_TOKEN_TELEMETRY", diagnostic["usage_basis"])
        self.assertEqual({"input_tokens": 20481, "cached_input_tokens": 7936, "cache_write_input_tokens": 0, "output_tokens": 10, "reasoning_output_tokens": 0}, diagnostic["host_usage"])
        self.assertEqual("UNAVAILABLE", diagnostic["provider_dollar_cost"])
        self.assertEqual("UNAVAILABLE", diagnostic["host_model_identity"])
        self.assertEqual("UNAVAILABLE / NOT DECOMPOSABLE", diagnostic["ambient_host_input_composition"])
        self.assertFalse(diagnostic["behavioral_trial_counted"])
        self.assertFalse(diagnostic["nexus_model_receipt_created"])
        self.assertEqual(0, self.result["formal_trial_count"])
        self.assertEqual(0, self.result["eligible_formal_trial_count"])
        self.assertEqual(0, self.result["nexus_model_receipt_count"])
        self.assertEqual("SUPPORTED_BY_REAL_HOST_PILOT", self.result["gates"]["G1_EXPLICIT_EXPERIMENT_PACKET_DELIVERY"])
        self.assertEqual("UNAVAILABLE", self.result["gates"]["G2_FULL_MODEL_VISIBLE_CONTEXT_PROOF"])

    def test_external_controller_runbook_forbids_agent_shell_and_preserves_child_sandbox(self):
        runbook = RUNBOOK.read_text(encoding="utf-8")
        self.assertIn("ordinary Windows PowerShell", runbook)
        self.assertIn("never from a Codex Agent/integrated shell", runbook)
        self.assertIn("current PowerShell process only", runbook)
        self.assertIn("--sandbox read-only", runbook)
        self.assertIn("--ephemeral", runbook)
        self.assertIn("first eligible formal matrix execution attempt", runbook)

    def test_committed_result_contains_no_absolute_local_paths(self):
        serialized = json.dumps(self.result)
        self.assertNotIn("C:\\Users\\", serialized)
        self.assertNotIn("/Users/", serialized)
        self.assertNotIn("/home/", serialized)
        self.assertNotIn(str(Path.home()), serialized)
        self.assertNotIn(str(Path(tempfile.gettempdir())), serialized)
        self.assertEqual(PRIVATE_CAPTURE_LOCATION, self.result["raw_capture_location"])
        self.assertEqual(CODEX_EXECUTABLE_OBSERVATION, self.result["codex_executable_observation"])

    def test_raw_capture_directory_must_remain_outside_repository(self):
        temp_capture = Path(tempfile.gettempdir()) / "nexus-academy-phase5-private-capture-test"
        self.assertNotIn(ROOT.resolve(), _external_capture_dir(temp_capture).parents)
        with self.assertRaises(RuntimeError):
            _external_capture_dir(ROOT)

    def test_evaluator_is_deterministic_and_tracks_unsupported_answers(self):
        expected = {"route": "UNKNOWN"}
        good = score('{"route":"UNKNOWN"}', expected, ["LOCAL-K2"])
        bad = score('{"route":"LOCAL-K2"}', expected, ["LOCAL-K2"])
        malformed = score("not json", expected, [])
        self.assertTrue(good["exact_correct"])
        self.assertEqual(0, good["unsupported_assertion_count"])
        self.assertEqual(1, bad["unsupported_assertion_count"])
        self.assertEqual(1, bad["distractor_adoption_count"])
        self.assertFalse(malformed["format_valid"])


if __name__ == "__main__":
    unittest.main()
