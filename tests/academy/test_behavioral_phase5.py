import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.eval.run_behavioral_phase5 import (
    CODEX_EXECUTABLE_OBSERVATION,
    PRIVATE_CAPTURE_LOCATION,
    _external_capture_dir,
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

    def test_fixture_hash_is_frozen_and_preregistration_has_no_receipts_or_trials(self):
        digest = hashlib.sha256(PACKETS.read_bytes()).hexdigest()
        self.assertEqual(digest, self.result["fixture_sha256"])
        self.assertEqual(18, self.packets["packet_count"])
        self.assertEqual("PHASE5_PREREGISTRATION_V2", self.packets["protocol_version"])
        self.assertEqual("PHASE5_PREREGISTRATION_V2", self.result["protocol_version"])
        self.assertEqual("REPLACED BEFORE ANY FORMAL BEHAVIORAL TRIAL", self.result["protocol_history"]["PHASE5_PREREGISTRATION_V1"])
        self.assertEqual("IN PROGRESS / PREREGISTERED V2", self.result["status"])
        self.assertEqual(0, self.result["execution_count"])
        self.assertEqual(0, self.result["formal_trial_count"])
        self.assertEqual(0, self.result["attempted_count"])
        self.assertEqual(0, self.result["nexus_model_receipt_count"])
        self.assertFalse(self.result["manual_host_runs_required"])

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

    def test_v2_changes_only_packet_metadata_and_execution_order(self):
        rows = sorted(self.packets["context_packets"], key=lambda row: row["trial_id"])
        packet_text_digest = hashlib.sha256("\n".join(row["trial_id"] + "\0" + row["packet_text"] for row in rows).encode() + b"\n").hexdigest()
        self.assertEqual("66f3217dc376519668bc2e415904923e7d09dc7c1a3a0bef4a4b2f5ae6f3bdb6", packet_text_digest)
        self.assertEqual("96e919d4798ebea839e6c58949c7fa1706d53806d3dc5041a71476b7a35ff25c", hashlib.sha256(EVALUATOR.read_bytes()).hexdigest())

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
        self.assertEqual("SUPPORTED_BY_REAL_HOST_PILOT; FORMAL MATRIX NOT EXECUTED", self.result["gates"]["G1_EXPLICIT_EXPERIMENT_PACKET_DELIVERY"])
        self.assertEqual("UNAVAILABLE", self.result["gates"]["G2_FULL_MODEL_VISIBLE_CONTEXT_PROOF"])
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
        self.assertTrue(phases["5"].startswith("IN PROGRESS / PREREGISTERED V2"))
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
        self.assertIn("fallback artifact", runbook.lower())

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
