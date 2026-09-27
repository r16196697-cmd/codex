import hashlib
import json
import re
import unittest
from pathlib import Path

from scripts.eval.run_presence_regression_phase6 import score_trial, validate_preregistration


ROOT = Path(__file__).resolve().parents[2]
PHASE6_PACKETS = ROOT / "eval/academy/fixtures/presence-regression-phase6-packets.json"
PHASE6_EVALUATOR = ROOT / "eval/academy/fixtures/presence-regression-phase6-evaluator.json"
PHASE6_RESULT = ROOT / "eval/academy/results/presence-regression-phase6.json"
PHASE5_PACKETS = ROOT / "eval/academy/fixtures/behavioral-phase5-packets.json"
PHASE5_EVALUATOR = ROOT / "eval/academy/fixtures/behavioral-phase5-evaluator.json"
PHASE5_RESULT = ROOT / "eval/academy/results/behavioral-phase5.json"
PHASE5_DOC = ROOT / "eval/academy/behavioral-phase5.md"
SYNTHESIS = ROOT / "eval/academy/bootstrap-academy-synthesis.md"
CANDIDATES = ROOT / "eval/academy/results/bootstrap-academy-candidates.json"
PHASE6_DOC = ROOT / "eval/academy/presence-regression-phase6.md"


class PresenceRegressionPhase6Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packets = json.loads(PHASE6_PACKETS.read_text(encoding="utf-8"))
        cls.evaluator = json.loads(PHASE6_EVALUATOR.read_text(encoding="utf-8"))
        cls.result = json.loads(PHASE6_RESULT.read_text(encoding="utf-8"))
        cls.phase5_packets = json.loads(PHASE5_PACKETS.read_text(encoding="utf-8"))
        cls.phase5_evaluator = json.loads(PHASE5_EVALUATOR.read_text(encoding="utf-8"))
        cls.phase5_result = json.loads(PHASE5_RESULT.read_text(encoding="utf-8"))

    def test_phase6_is_preregistration_only_and_phase5_is_closed(self):
        self.assertEqual("PHASE6_PRESENCE_REGRESSION_PREREG_V1", self.result["protocol_version"])
        self.assertEqual("PRESENCE_REGRESSION_EVAL_V1", self.evaluator["evaluator_version"])
        self.assertEqual("IN PROGRESS / PREREGISTRATION — EXTERNAL REVIEW PENDING", self.result["status"])
        self.assertFalse(self.result["execution_authorized"])
        self.assertEqual(0, self.result["formal_trial_count"])
        self.assertEqual(0, self.result["execution_count"])
        self.assertEqual(0, self.result["nexus_model_receipt_count"])
        self.assertEqual([], self.result.get("formal_trials", []))
        self.assertEqual("PHASE5_OFFICIAL_HOST_INVOCATION_V1", self.result["invocation_spec_id"])
        self.assertEqual("CLOSED / ACCEPTED", self.phase5_result["status"])
        self.assertEqual("CLOSED / ACCEPTED", self.result["phase5_closure"]["status"])
        self.assertEqual("NOT STARTED", self.result["capability_certification"])
        self.assertEqual("NOT STARTED", self.result["shadow"])
        self.assertEqual("NOT STARTED", self.result["production_qualification"])

    def test_frozen_phase5_hashes_and_observations_are_unchanged_and_classified(self):
        self.assertEqual("66f3217dc376519668bc2e415904923e7d09dc7c1a3a0bef4a4b2f5ae6f3bdb6", self.phase5_result["model_visible_packet_text_sha256"])
        self.assertEqual("f41f960addbef6580102ca382bfc9b6dff90c6285afadaed18335bd1b2d8f00e", hashlib.sha256(PHASE5_PACKETS.read_bytes()).hexdigest())
        self.assertEqual("96e919d4798ebea839e6c58949c7fa1706d53806d3dc5041a71476b7a35ff25c", hashlib.sha256(PHASE5_EVALUATOR.read_bytes()).hexdigest())
        self.assertEqual("579b320800cfddc2d48db7ed1dcc645419614c9e059310a90cfeeaa0e743c3c6", self.phase5_result["execution_order_sha256"])
        l73 = self.phase5_result["context_trials"]["L73"]
        self.assertEqual('{"retention_days":"23 days","handoff":"MIRA-Q8","region":"LCL-6"}', l73["output"])
        self.assertFalse(l73["exact_correct"])
        self.assertEqual(2 / 3, l73["required_fact_coverage"])
        self.assertEqual(1, l73["unsupported_assertion_count"])
        closure_l73 = self.phase5_result["external_review_closure"]["findings"]["L73"]
        self.assertEqual("EXACT_CANONICALIZATION_MISMATCH", closure_l73["qualitative_classification"])
        self.assertFalse(closure_l73["frozen_score_unchanged"]["exact_correct"])
        self.assertFalse(closure_l73["scoring_changed"])

        w34 = self.phase5_result["presence_trials"]["W34"]
        self.assertEqual('{"owner":"RPAL-632"}', w34["output"])
        w34_packet = next(row["packet_text"] for row in self.phase5_packets["context_packets"] if row["trial_id"] == "W34")
        shifts = re.search(r"move it forward (\d+) positions.*?add (\d+) modulo 10", w34_packet)
        self.assertIsNotNone(shifts)
        letter_shift, digit_shift = map(int, shifts.groups())
        transformed = "".join(
            chr((ord(ch) - ord("A") + letter_shift) % 26 + ord("A")) if "A" <= ch <= "Z"
            else str((int(ch) + digit_shift) % 10) if ch.isdigit()
            else ch
            for ch in "KITE-309"
        )
        self.assertEqual("RPAL-632", transformed)
        w34_closure = self.phase5_result["external_review_closure"]["findings"]["W34"]
        self.assertEqual("INSTRUCTION_BLEED_OBSERVATION / PRESENCE_REGRESSION_OBSERVED", w34_closure["qualitative_classification"])
        self.assertTrue(w34_closure["output_matches_frozen_glyph_shift_transform"])
        self.assertEqual("UNCHARACTERIZED", w34_closure["cross_session_memory_confound"])
        self.assertFalse(w34_closure["causal_claim_allowed"])

    def test_packet_ids_mapping_pairing_and_semantic_order_are_frozen(self):
        rows = self.packets["packets"]
        order = self.packets["execution_order"]
        ids = [row["trial_id"] for row in rows]
        self.assertEqual(24, len(ids))
        self.assertEqual(24, len(set(ids)))
        self.assertEqual(set(ids), set(order))
        self.assertEqual(set(ids), set(self.evaluator["trials"]))
        self.assertEqual(4, len(self.packets["families"]))
        self.assertEqual(24, self.evaluator["trial_count"])
        self.assertEqual(12, self.result["relevant_control_pair_count"])
        order_hash = hashlib.sha256(("\n".join(order) + "\n").encode("utf-8")).hexdigest()
        self.assertEqual(order_hash, self.packets["execution_order_sha256"])
        self.assertEqual(order_hash, self.result["execution_order_sha256"])
        self.assertEqual("f48fca58c13f6e1008bdd3fbb766d45a39e954c6d2837dac75e2fd997ec71d04", order_hash)

        position = {trial_id: index for index, trial_id in enumerate(order)}
        for family in (row["family_id"] for row in self.packets["families"]):
            for task_kind in ("CAPABILITY_RELEVANT", "UNRELATED_CONTROL"):
                family_rows = [row for row in rows if row["family_id"] == family and row["task_kind"] == task_kind]
                self.assertEqual({"P0_ABSENT", "P1_METADATA_ONLY", "P2_FULL_INSTRUCTION"}, {row["presence"] for row in family_rows})
                ordered = sorted(family_rows, key=lambda row: position[row["trial_id"]])
                self.assertEqual(["P0_ABSENT", "P1_METADATA_ONLY", "P2_FULL_INSTRUCTION"], [row["presence"] for row in ordered])
            for level in ("P0_ABSENT", "P1_METADATA_ONLY", "P2_FULL_INSTRUCTION"):
                self.assertEqual(1, sum(row["family_id"] == family and row["presence"] == level and row["task_kind"] == "CAPABILITY_RELEVANT" for row in rows))
                self.assertEqual(1, sum(row["family_id"] == family and row["presence"] == level and row["task_kind"] == "UNRELATED_CONTROL" for row in rows))
        self.assertEqual(order, [row["trial_id"] for row in rows])
        self.assertEqual(self.result["packet_fixture_sha256"], hashlib.sha256(PHASE6_PACKETS.read_bytes()).hexdigest())
        self.assertEqual(self.result["evaluator_sha256"], hashlib.sha256(PHASE6_EVALUATOR.read_bytes()).hexdigest())
        self.assertEqual("b91173582b6d68ccbb1c329d9dec753f0e129c9f6343f6bd320615683f3262b0", self.result["packet_fixture_sha256"])
        self.assertEqual("fa67f7f99cb649ac2b36bc92b17783b5cf530d4ade8f0de5a55e596c8824356a", self.result["evaluator_sha256"])

    def test_packets_hide_conditions_and_scoring_metadata_and_have_fresh_values(self):
        labels = ("P0_ABSENT", "P1_METADATA_ONLY", "P2_FULL_INSTRUCTION")
        text_hashes = []
        visible = []
        token_values = []
        for row in self.packets["packets"]:
            text = row["model_visible_text"]
            visible.append(text)
            text_hashes.append(hashlib.sha256(text.encode("utf-8")).hexdigest())
            self.assertFalse(any(label in text for label in labels), row["trial_id"])
            self.assertNotRegex(text, r"(?i)expected|ground.?truth|scoring|evaluator|correct answer")
            self.assertNotRegex(text, r"(?i)(api[_ -]?key|password|cookie|credential|authorization|https?://|www\.)")
            self.assertNotRegex(text, r"(?:[A-Z]:\\Users\\|/Users/|/home/)")
            self.assertNotRegex(text, r"(?i)\b(network|filesystem|file system|write|save|delete|shell|terminal|install|tool call|authenticate|login)\b")
            self.assertNotIn("expected", row)
            if row["task_kind"] == "CAPABILITY_RELEVANT":
                if row["family_id"] == "VARNET_CIPHER":
                    token_values.append(re.search(r"code ([A-Z]+)", text).group(1))
                elif row["family_id"] == "OVRIN_PROJECTION":
                    token_values.extend(json.loads(re.search(r"record: (\{.*?\})\.", text).group(1)).values())
                elif row["family_id"] == "NIMBEL_ORDER":
                    token_values.extend(json.loads(re.search(r"list: (\[.*?\])\.", text).group(1)))
                else:
                    token_values.append(re.search(r"digit string (\d+)", text).group(1))
            else:
                token_values.append(re.search(r"Record: \{\"owner\":\"([^\"]+)\"", text).group(1))
        self.assertEqual(24, len(set(text_hashes)))
        self.assertEqual(len(token_values), len(set(token_values)))
        self.assertFalse(any(value in " ".join(visible) for value in ("VEIL-739", "MIRA-Q8", "LOCAL-K2", "RPAL-632", "KITE-309")))
        self.assertEqual(24, len(self.result["frozen_packet_text_sha256_by_trial"]))
        for family in self.packets["families"]:
            self.assertNotRegex(family["metadata"] + family["instruction"], r"(?i)(https?://|api[_ -]?key|password|cookie|credential|authorization|filesystem|file system|write file|shell|terminal|tool call)")

    def test_deterministic_scorer_separates_utility_and_control_preservation(self):
        p2_utility = score_trial('{"result":"CYTF"}', {"result": "CYTF"}, "CAPABILITY_RELEVANT", "P2_FULL_INSTRUCTION")
        self.assertTrue(p2_utility["relevant_utility"])
        self.assertEqual("NOT_APPLICABLE", p2_utility["unrelated_task_preservation"])
        p2_regression = score_trial('{"owner":"RPAL-632"}', {"owner": "KITE-309"}, "UNRELATED_CONTROL", "P2_FULL_INSTRUCTION")
        self.assertTrue(p2_regression["format_valid"])
        self.assertFalse(p2_regression["exact_correct"])
        self.assertFalse(p2_regression["unrelated_task_preservation"])
        self.assertEqual("PRESENCE_REGRESSION_OBSERVED", p2_regression["presence_regression"])
        p0_control = score_trial('{"owner":"QEN-811"}', {"owner": "QEN-811"}, "UNRELATED_CONTROL", "P0_ABSENT")
        self.assertTrue(p0_control["unrelated_task_preservation"])
        self.assertEqual("NOT_OBSERVED", p0_control["presence_regression"])
        wrong_type = score_trial('{"result":1}', {"result": True}, "CAPABILITY_RELEVANT", "P2_FULL_INSTRUCTION")
        self.assertFalse(wrong_type["format_valid"])

    def test_offline_runner_has_no_host_execution_path(self):
        summary = validate_preregistration()
        self.assertEqual(24, summary["packet_count"])
        self.assertEqual(4, summary["family_count"])
        self.assertEqual(12, summary["relevant_control_pair_count"])
        self.assertEqual(0, summary["formal_trial_count"])
        self.assertEqual(0, summary["nexus_model_receipt_count"])
        self.assertFalse(summary["host_execution_authorized"])
        runner_source = (ROOT / "scripts/eval/run_presence_regression_phase6.py").read_text(encoding="utf-8")
        self.assertNotIn("import subprocess", runner_source)
        self.assertNotIn("subprocess.run(", runner_source)
        self.assertNotIn("--execute-host", runner_source)
        self.assertNotIn("codex exec", runner_source)

    def test_candidate_statuses_skill_screen_and_phase_gates(self):
        candidates = json.loads(CANDIDATES.read_text(encoding="utf-8"))
        ledger = {item["candidate_id"]: item for item in candidates["candidates"]}
        self.assertEqual("CANDIDATE / PREREGISTRATION_REQUIRED", ledger["PRESENCE_REGRESSION_GATE"]["status"])
        self.assertFalse(ledger["PRESENCE_REGRESSION_GATE"]["promotion_allowed"])
        self.assertIn("W34", ledger["PRESENCE_REGRESSION_GATE"]["evidence_available"])
        self.assertEqual("INSUFFICIENT_EVIDENCE", ledger["OUTPUT_CANONICALIZATION_POLICY"]["status"])
        self.assertFalse(ledger["OUTPUT_CANONICALIZATION_POLICY"]["promotion_allowed"])
        screen = self.result["real_skill_screening"]
        self.assertEqual(136, screen["screened_instruction_artifacts"])
        self.assertEqual({"CODEX_SKILLS": 110, "BUNDLED_PLUGIN_SKILLS": 2, "CURATED_PLUGIN_SKILLS": 24}, screen["source_counts"])
        self.assertEqual(0, screen["selected_candidate_count"])
        self.assertEqual([], screen["selected_candidates"])
        self.assertEqual("DISCOVERED / UNEVALUATED", self.result["all_discovered_skills_lifecycle"])
        self.assertEqual("DISCOVERED / UNEVALUATED", self.result["project_experience_curator_status"])
        self.assertEqual("NOT STARTED", candidates["capability_certification"]["status"])
        phase5_phase = next(row for row in candidates["phases"] if row["phase"] == "5")
        phase6_phase = next(row for row in candidates["phases"] if row["phase"] == "6")
        self.assertEqual("CLOSED / ACCEPTED", phase5_phase["status"])
        self.assertIn("EXTERNAL REVIEW PENDING", phase6_phase["status"])
        phase5_doc = PHASE5_DOC.read_text(encoding="utf-8")
        phase6_doc = PHASE6_DOC.read_text(encoding="utf-8")
        synthesis = SYNTHESIS.read_text(encoding="utf-8")
        self.assertIn("CLOSED / ACCEPTED", phase5_doc)
        self.assertIn("CLOSED / ACCEPTED", synthesis)
        self.assertIn("IN PROGRESS / PREREGISTRATION", phase6_doc)
        self.assertIn("NATIVE_HOST_SKILL_LOADED_STATE_VERIFIED", phase6_doc)
        self.assertIn("No Academy-wide PASS", phase6_doc)


if __name__ == "__main__":
    unittest.main()
