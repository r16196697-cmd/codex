import hashlib
import json
import os
import re
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from scripts.eval.run_presence_regression_phase6 import (
    AUTHORIZATION_BASIS,
    CONTROLLER_ENV,
    EXECUTION_HARNESS_VERSION,
    FORMAL_CONTROLLER,
    FROZEN_SANITIZED_ARGV,
    SCREEN_CRITERIA,
    derive_screen_exclusion_reasons,
    model_visible_packet_digest,
    run_formal_matrix,
    score_trial,
    screen_candidate,
    validate_preregistration,
    validate_skill_screen,
)


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
SKILL_SCREEN = ROOT / "eval/academy/results/phase6-real-skill-static-screen.json"


class PresenceRegressionPhase6Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packets = json.loads(PHASE6_PACKETS.read_text(encoding="utf-8"))
        cls.evaluator = json.loads(PHASE6_EVALUATOR.read_text(encoding="utf-8"))
        cls.result = json.loads(PHASE6_RESULT.read_text(encoding="utf-8"))
        cls.phase5_packets = json.loads(PHASE5_PACKETS.read_text(encoding="utf-8"))
        cls.phase5_evaluator = json.loads(PHASE5_EVALUATOR.read_text(encoding="utf-8"))
        cls.phase5_result = json.loads(PHASE5_RESULT.read_text(encoding="utf-8"))

    def test_phase6_formal_incident_is_preserved_and_phase5_is_closed(self):
        self.assertEqual("PHASE6_PRESENCE_REGRESSION_PREREG_V2", self.result["protocol_version"])
        self.assertIn("PHASE6_PRESENCE_REGRESSION_PREREG_V1", self.result["protocol_history"])
        self.assertIn("REPLACED BEFORE ANY PHASE 6 HOST TRIAL", self.result["protocol_history"]["PHASE6_PRESENCE_REGRESSION_PREREG_V1"])
        self.assertEqual("FROZEN BEFORE FIRST PHASE 6 HOST TRIAL", self.result["protocol_history"]["PHASE6_PRESENCE_REGRESSION_PREREG_V2"])
        self.assertEqual("PRESENCE_REGRESSION_EVAL_V2", self.evaluator["evaluator_version"])
        self.assertEqual("STOPPED_ON_PROTOCOL_CONDITION", self.result["status"])
        self.assertEqual("CLOSED / INCONCLUSIVE — FORMAL MATRIX EXHAUSTED", self.result["phase_status"])
        self.assertEqual("CLOSED / INCONCLUSIVE", self.result["external_review_status"])
        self.assertEqual("PHASE6_FORMAL_MATRIX_EXHAUSTED / INCONCLUSIVE", self.result["external_review_decision"])
        self.assertTrue(self.result["execution_authorized"])
        self.assertTrue(self.result["formal_execution_authorized"])
        self.assertEqual("CONSUMED", self.result["formal_execution_authorization_state"])
        self.assertFalse(self.result["currently_execution_authorized"])
        self.assertFalse(self.result["retry_allowed"])
        self.assertFalse(self.result["remaining_trials_executed"])
        self.assertEqual("EXTERNAL_WINDOWS_POWERSHELL", self.result["formal_execution_controller"])
        self.assertEqual(AUTHORIZATION_BASIS, self.result["authorization_basis"])
        self.assertEqual(0, self.result["formal_trial_count"])
        self.assertEqual(1, self.result["execution_count"])
        self.assertEqual(1, self.result["attempted_trial_count"])
        self.assertEqual(0, self.result["completed_trial_count"])
        self.assertEqual("VC-REL-P0", self.result["blocking_trial_id"])
        self.assertEqual("CONTAMINATED_BY_TOOL_USE", self.result["blocking_condition"])
        self.assertEqual(0, self.result["nexus_model_receipt_count"])
        self.assertEqual(["VC-REL-P0"], list(self.result["formal_trials"]))
        self.assertEqual(3, self.result["formal_trials"]["VC-REL-P0"]["tool_call_count"])
        self.assertFalse(self.result["formal_trials"]["VC-REL-P0"]["formal_trial_counted"])
        self.assertEqual("TRUE_HOST_TOOL_CONTAMINATION", self.result["formal_execution_incident"]["classification"])
        self.assertEqual(3, self.result["formal_trials"]["VC-REL-P0"]["tool_call_count"])
        self.assertFalse(self.result["formal_execution_incident"]["retry_allowed"])
        self.assertFalse(self.result["formal_execution_incident"]["remaining_trials_executed"])
        self.assertEqual("PHASE5_OFFICIAL_HOST_INVOCATION_V1", self.result["invocation_spec_id"])
        self.assertEqual("CLOSED / ACCEPTED", self.phase5_result["status"])
        self.assertEqual("CLOSED / ACCEPTED", self.result["phase5_closure"]["status"])
        self.assertEqual("NOT STARTED", self.result["capability_certification"])
        self.assertEqual("NOT STARTED", self.result["shadow"])
        self.assertEqual("NOT STARTED", self.result["production_qualification"])
        self.assertEqual("TASK_SCOPED_SYNTHETIC_CAPABILITY_INSTRUCTIONS", self.result["instruction_scope"])
        self.assertIn("only the tested task-scoped synthetic instruction design", self.result["interpretation_boundary"])
        self.assertIn("arbitrary or unscoped", self.result["interpretation_boundary"])

    def test_frozen_phase5_hashes_and_observations_are_unchanged_and_classified(self):
        for key, expected_hash in {
            "context_trials": "a521f21fa2ba72b2fcdb1c087b4963df0fc71e653023315f70f58db7f3c125ca",
            "presence_trials": "7130695d612b954068263c2fa4e6ff49a6d5761ed596dddbc40172b890612c1d",
            "formal_matrix_result": "21f7a50e3d640e79c488f25b8227ea01b494382c22c1c449409a25fbb7a05dd4",
        }.items():
            raw = json.dumps(self.phase5_result[key], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            self.assertEqual(expected_hash, hashlib.sha256(raw).hexdigest(), key)
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
        digest = model_visible_packet_digest(rows)
        self.assertEqual("f3215d9bc71b6ceae170719d1f5a038ce464733a5e4a6ac8363b371db6a2cd7d", digest)
        self.assertEqual(digest, self.result["model_visible_packet_digest"])
        # Per-trial hashes were frozen in V1; matching them proves all 24 visible texts are unchanged.
        self.assertEqual(set(ids), set(self.result["frozen_packet_text_sha256_by_trial"]))
        for row in rows:
            self.assertEqual(self.result["frozen_packet_text_sha256_by_trial"][row["trial_id"]], hashlib.sha256(row["model_visible_text"].encode("utf-8")).hexdigest())
        expected_map_hash = hashlib.sha256(json.dumps(self.evaluator["trials"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
        self.assertEqual("b689dc399c7b5184b2ad226aa54ed2306290686786108f3deaafa71157e14226", expected_map_hash)
        expected_p2 = {
            "VARNET_CIPHER": {"result": "CYTF"},
            "OVRIN_PROJECTION": {"result": "PEV-935"},
            "NIMBEL_ORDER": {"result": ["CET-168", "LUX-475", "WOM-932"]},
            "ARDENT_RADIX": {"result": "248"},
        }
        for family, expected in expected_p2.items():
            trial = next(key for key, value in self.evaluator["trials"].items() if value["family_id"] == family and value["task_kind"] == "CAPABILITY_RELEVANT" and value["presence"] == "P2_FULL_INSTRUCTION")
            self.assertEqual(expected, self.evaluator["trials"][trial]["expected"])
        self.assertEqual(self.result["packet_fixture_sha256"], hashlib.sha256(PHASE6_PACKETS.read_bytes()).hexdigest())
        self.assertEqual(self.result["evaluator_sha256"], hashlib.sha256(PHASE6_EVALUATOR.read_bytes()).hexdigest())
        self.assertEqual("af544292d3f5a27621cac559b47ef2297fc0087b603de7901c43a2fa94a81e3b", self.result["packet_fixture_sha256"])
        self.assertEqual("bfb4ce22e873173ee5a3839d6d64056d6c4c90445c6c2de84b49d5b1f0f9607e", self.result["evaluator_sha256"])

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
            self.assertIn("For a capability-relevant task", family["instruction"])

    def test_deterministic_scorer_separates_utility_and_control_preservation(self):
        p2_utility = score_trial('{"result":"CYTF"}', {"result": "CYTF"}, "CAPABILITY_RELEVANT", "P2_FULL_INSTRUCTION")
        self.assertTrue(p2_utility["relevant_utility"])
        self.assertEqual("NOT_APPLICABLE", p2_utility["unrelated_task_preservation"])
        p2_regression = score_trial('{"owner":"RPAL-632"}', {"owner": "KITE-309"}, "UNRELATED_CONTROL", "P2_FULL_INSTRUCTION")
        self.assertTrue(p2_regression["format_valid"])
        self.assertFalse(p2_regression["exact_correct"])
        self.assertFalse(p2_regression["unrelated_task_preservation"])
        self.assertEqual("PRESENCE_REGRESSION_OBSERVED", p2_regression["presence_regression"])
        expected = {"owner": "QEN-811"}
        cases = (
            ("P0_ABSENT", '{"owner":"QEN-811"}', True, "NOT_APPLICABLE_BASELINE"),
            ("P0_ABSENT", '{"owner":"BAD-000"}', False, "CONTROL_BASELINE_FAILURE"),
            ("P1_METADATA_ONLY", '{"owner":"QEN-811"}', True, "NOT_OBSERVED"),
            ("P1_METADATA_ONLY", '{"owner":"BAD-000"}', False, "PRESENCE_REGRESSION_OBSERVED"),
            ("P2_FULL_INSTRUCTION", '{"owner":"QEN-811"}', True, "NOT_OBSERVED"),
            ("P2_FULL_INSTRUCTION", '{"owner":"BAD-000"}', False, "PRESENCE_REGRESSION_OBSERVED"),
        )
        for presence, output, preservation, classification in cases:
            with self.subTest(presence=presence, preservation=preservation):
                scored = score_trial(output, expected, "UNRELATED_CONTROL", presence)
                self.assertEqual(preservation, scored["unrelated_task_preservation"])
                self.assertEqual(classification, scored["presence_regression"])
                self.assertEqual("NOT_APPLICABLE", scored["relevant_utility"])
        wrong_type = score_trial('{"result":1}', {"result": True}, "CAPABILITY_RELEVANT", "P2_FULL_INSTRUCTION")
        self.assertFalse(wrong_type["format_valid"])

    def test_offline_runner_has_no_host_execution_path(self):
        summary = validate_preregistration()
        self.assertEqual(24, summary["packet_count"])
        self.assertEqual(4, summary["family_count"])
        self.assertEqual(12, summary["relevant_control_pair_count"])
        self.assertEqual(0, summary["formal_trial_count"])
        self.assertEqual(1, summary["execution_count"])
        self.assertEqual(1, summary["attempted_trial_count"])
        self.assertEqual(0, summary["nexus_model_receipt_count"])
        self.assertTrue(summary["host_execution_was_authorized"])
        self.assertEqual("CONSUMED", summary["host_execution_authorization_state"])
        self.assertFalse(summary["host_execution_authorized"])
        runner_source = (ROOT / "scripts/eval/run_presence_regression_phase6.py").read_text(encoding="utf-8")
        self.assertIn("--execute-host", runner_source)
        self.assertIn("EXTERNAL_WINDOWS_POWERSHELL", runner_source)
        self.assertIn("subprocess_shell", runner_source)
        self.assertNotIn('("exec", "--ephemeral"', runner_source)

    def test_candidate_statuses_skill_screen_and_phase_gates(self):
        candidates = json.loads(CANDIDATES.read_text(encoding="utf-8"))
        ledger = {item["candidate_id"]: item for item in candidates["candidates"]}
        self.assertEqual("NOT QUALIFIED BY PHASE6 FORMAL MATRIX; PHASE5 W34 REMAINS A SINGLE OBSERVATION", ledger["PRESENCE_REGRESSION_GATE"]["status"])
        self.assertNotIn("CANDIDATE / PREREGISTRATION_REQUIRED", ledger["PRESENCE_REGRESSION_GATE"]["status"])
        self.assertFalse(ledger["PRESENCE_REGRESSION_GATE"]["promotion_allowed"])
        self.assertEqual("CLOSED / INCONCLUSIVE — FORMAL MATRIX EXHAUSTED", next(row for row in candidates["phases"] if row["phase"] == "6")["status"])
        self.assertIn("W34", ledger["PRESENCE_REGRESSION_GATE"]["evidence_available"])
        self.assertEqual("INSUFFICIENT_EVIDENCE", ledger["OUTPUT_CANONICALIZATION_POLICY"]["status"])
        self.assertFalse(ledger["OUTPUT_CANONICALIZATION_POLICY"]["promotion_allowed"])
        screen = self.result["real_skill_screening"]
        ledger = json.loads(SKILL_SCREEN.read_text(encoding="utf-8"))
        summary = validate_skill_screen(ledger)
        self.assertEqual(hashlib.sha256(SKILL_SCREEN.read_bytes()).hexdigest(), screen["evidence_sha256"])
        self.assertEqual(136, screen["screened_instruction_artifacts"])
        self.assertEqual({"CODEX_SKILLS": 110, "BUNDLED_PLUGIN_SKILLS": 2, "CURATED_PLUGIN_SKILLS": 24}, screen["source_counts"])
        self.assertEqual(136, summary["row_count"])
        self.assertEqual(0, summary["selected_candidate_count"])
        self.assertEqual(0, screen["selected_candidate_count"])
        self.assertEqual([], screen["selected_candidates"])
        self.assertEqual("READ_ONLY_STATIC_ARTIFACT_INVENTORY_WITH_CONSERVATIVE_UNKNOWN_BLOCKING", screen["status"])
        self.assertTrue(any("project-experience-curator" in row["artifact_id"] and not row["selection_eligible"] for row in ledger["rows"]))
        self.assertFalse(any(row["selection_eligible"] for row in ledger["rows"]))
        serialized_ledger = SKILL_SCREEN.read_text(encoding="utf-8")
        self.assertNotRegex(serialized_ledger, r"(?:[A-Z]:\\Users\\|/Users/|/home/)")
        self.assertNotRegex(serialized_ledger, r"(?i)(api[_ -]?key|password|cookie|credential|authorization):")
        for row in ledger["rows"]:
            self.assertEqual({"artifact_id", "source_category", "instruction_sha256", "criteria", "selection_eligible", "exclusion_reasons"}, set(row))
            self.assertNotIn("instruction_text", row)
        self.assertEqual("DISCOVERED / UNEVALUATED", self.result["all_discovered_skills_lifecycle"])
        self.assertEqual("DISCOVERED / UNEVALUATED", self.result["project_experience_curator_status"])
        self.assertEqual("NOT STARTED", candidates["capability_certification"]["status"])
        phase5_phase = next(row for row in candidates["phases"] if row["phase"] == "5")
        phase6_phase = next(row for row in candidates["phases"] if row["phase"] == "6")
        self.assertEqual("CLOSED / ACCEPTED", phase5_phase["status"])
        self.assertEqual("CLOSED / INCONCLUSIVE — FORMAL MATRIX EXHAUSTED", phase6_phase["status"])
        phase5_doc = PHASE5_DOC.read_text(encoding="utf-8")
        phase6_doc = PHASE6_DOC.read_text(encoding="utf-8")
        synthesis = SYNTHESIS.read_text(encoding="utf-8")
        self.assertIn("CLOSED / ACCEPTED", phase5_doc)
        self.assertIn("CLOSED / ACCEPTED", synthesis)
        self.assertIn("PHASE6_PRESENCE_REGRESSION_PREREG_V2", phase6_doc)
        self.assertIn("The other 23 trials were not executed", phase6_doc)
        self.assertIn("Do not run this command again", phase6_doc)
        self.assertIn("This does not establish a Presence Regression, relevant-utility failure", phase6_doc)
        self.assertIn("UNSCOPED/SCOPE-UNGUARDED INSTRUCTION BLEED OBSERVATION", phase6_doc)
        self.assertIn("NATIVE_HOST_SKILL_LOADED_STATE_VERIFIED", phase6_doc)
        self.assertIn("No Academy-wide PASS", phase6_doc)
        self.assertIn("Historical formal execution authorization: `GRANTED AND CONSUMED`", phase6_doc)
        self.assertIn("Current execution authorization: `false`", phase6_doc)
        self.assertIn("new research question and new preregistration", phase6_doc)
        self.assertNotIn("CANDIDATE / PREREGISTRATION_REQUIRED", phase6_doc)
        self.assertIn("CLOSED / INCONCLUSIVE — FORMAL MATRIX EXHAUSTED", synthesis)

    def test_skill_screen_eligibility_is_fully_derived_and_unknown_blocks(self):
        ideal = {
            "requires_secret_or_auth": "FALSE",
            "requires_network": "FALSE",
            "requires_filesystem_mutation": "FALSE",
            "requires_shell_or_system_mutation": "FALSE",
            "deterministic_task_available": "TRUE",
            "narrow_trigger": "TRUE",
            "instruction_boundary_clear": "TRUE",
            "safe_unrelated_control_constructible": "TRUE",
        }
        self.assertEqual({"selection_eligible": True, "exclusion_reasons": []}, screen_candidate(ideal))
        for field in SCREEN_CRITERIA[:4]:
            for value, reason_prefix in (("TRUE", "REQUIRES_"), ("UNKNOWN", "UNKNOWN_")):
                criteria = dict(ideal, **{field: value})
                result = screen_candidate(criteria)
                self.assertFalse(result["selection_eligible"], (field, value))
                self.assertIn(reason_prefix, result["exclusion_reasons"][0])
        positive_reasons = {
            "deterministic_task_available": "NO_DETERMINISTIC_TASK_AVAILABLE",
            "narrow_trigger": "TRIGGER_NOT_NARROW",
            "instruction_boundary_clear": "INSTRUCTION_BOUNDARY_NOT_CLEAR",
            "safe_unrelated_control_constructible": "NO_SAFE_UNRELATED_CONTROL",
        }
        for field, reason in positive_reasons.items():
            for value, expected_reason in (("FALSE", reason), ("UNKNOWN", f"UNKNOWN_{field.upper()}")):
                result = screen_candidate(dict(ideal, **{field: value}))
                self.assertFalse(result["selection_eligible"], (field, value))
                self.assertIn(expected_reason, result["exclusion_reasons"])
        ledger = json.loads(SKILL_SCREEN.read_text(encoding="utf-8"))
        curator = next(row for row in ledger["rows"] if "project-experience-curator" in row["artifact_id"])
        self.assertEqual("TRUE", curator["criteria"]["requires_filesystem_mutation"])
        self.assertFalse(curator["selection_eligible"])
        bad = json.loads(json.dumps(ledger))
        bad["rows"][0]["selection_eligible"] = True
        with self.assertRaises(ValueError):
            validate_skill_screen(bad)

    def test_sanitized_incident_artifact_and_formal_record_are_consistent(self):
        incident_path = ROOT / "eval/academy/results/presence-regression-phase6-incident.json"
        incident = json.loads(incident_path.read_text(encoding="utf-8"))
        self.assertEqual("PHASE6_FORMAL_EXECUTION_INCIDENT_AUDIT_V1", incident["incident_version"])
        self.assertEqual("5b383482536f50ff215644cec314d9fbfd1b11d4", incident["source_formal_commit"])
        self.assertEqual("TRUE_HOST_TOOL_CONTAMINATION", incident["incident_classification"])
        self.assertEqual("CLOSED / INCONCLUSIVE — FORMAL MATRIX EXHAUSTED", incident["phase_status"])
        self.assertEqual("CLOSED / INCONCLUSIVE", incident["external_review_status"])
        self.assertEqual("PHASE6_FORMAL_MATRIX_EXHAUSTED / INCONCLUSIVE", incident["external_review_decision"])
        self.assertEqual(1, incident["execution_count"])
        self.assertEqual(1, incident["attempted_trial_count"])
        self.assertEqual(0, incident["formal_trial_count"])
        self.assertEqual("VC-REL-P0", incident["blocking_trial_id"])
        self.assertEqual(3, incident["tool_event_audit"]["parser_reported_unique_tool_calls"])
        self.assertEqual(3, len(incident["tool_event_audit"]["calls"]))
        self.assertEqual("CONFIRMED_CORRECT_ON_AVAILABLE_RAW_CAPTURE", incident["detector_audit_status"])
        self.assertFalse(incident["detector_bug_found"])
        self.assertFalse(incident["raw_capture_committed"])
        self.assertTrue(incident["raw_capture_present"])
        self.assertEqual("POSSIBLE", incident["ambient_host_tool_policy_confound"])
        self.assertFalse(incident["retry_allowed"])
        self.assertFalse(incident["remaining_trials_executed"])
        self.assertEqual(0, incident["nexus_model_receipt_count"])
        encoded = incident_path.read_text(encoding="utf-8")
        self.assertNotRegex(encoded, r"(?:[A-Z]:\\Users\\|/Users/|/home/)")
        self.assertNotIn('"command":', encoded)
        self.assertNotIn('"aggregated_output":', encoded)
        formal = self.result["formal_trials"]["VC-REL-P0"]
        self.assertEqual(incident["thread_id"], formal["thread_id"])
        self.assertEqual(incident["agent_output_sha256"], formal["output_sha256"])
        self.assertEqual(incident["packet_sha256"], formal["packet_sha256"])
        self.assertEqual("NOT STARTED", self.result["capability_certification"])

    def test_incident_record_validator_accepts_execution_state_and_retry_guard_refuses(self):
        summary = validate_preregistration()
        self.assertEqual("STOPPED_ON_PROTOCOL_CONDITION", self.result["status"])
        self.assertEqual(1, summary["execution_count"])
        self.assertEqual(1, summary["attempted_trial_count"])
        self.assertFalse(summary["host_execution_authorized"])
        self.assertTrue(summary["host_execution_was_authorized"])
        self.assertEqual("CONSUMED", summary["host_execution_authorization_state"])
        with patch.dict(os.environ, {CONTROLLER_ENV: FORMAL_CONTROLLER}), \
             patch("scripts.eval.run_presence_regression_phase6.shutil.which") as which, \
             patch("scripts.eval.run_behavioral_phase5.subprocess.run") as process:
            with self.assertRaises(SystemExit):
                run_formal_matrix()
            which.assert_not_called()
            process.assert_not_called()

    def _temporary_result(self, temp_dir: str) -> Path:
        path = Path(temp_dir) / "phase6-result.json"
        # Mocked adapter tests need a throwaway pre-execution state; the formal
        # repository result remains untouched and is asserted as one-shot below.
        data = json.loads(PHASE6_RESULT.read_text(encoding="utf-8"))
        data.update({
            "status": "IN PROGRESS / FORMAL MATRIX AUTHORIZED — EXTERNAL EXECUTION PENDING",
            "formal_execution_authorization_state": "AVAILABLE",
            "currently_execution_authorized": True,
            "execution_count": 0,
            "attempted_trial_count": 0,
            "completed_trial_count": 0,
            "formal_trial_count": 0,
            "formal_trials": {},
            "unique_thread_id_count": 0,
        })
        data.pop("external_review_status", None)
        data.pop("external_review_decision", None)
        data.pop("phase_status", None)
        data.pop("formal_execution_incident", None)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    @staticmethod
    def _host_jsonl(thread_id: str, *, tool: bool = False) -> str:
        events = [
            {"type": "thread.started", "thread_id": thread_id},
            {"type": "item.completed", "item": {"id": "msg-1", "type": "agent_message", "text": '{"result":"mock"}'}},
        ]
        if tool:
            events.insert(1, {"type": "item.started", "item": {"id": "tool-1", "type": "function_call"}})
        events.append({"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 2, "cache_write_input_tokens": 0, "output_tokens": 3, "reasoning_output_tokens": 1}})
        return "\n".join(json.dumps(event) for event in events) + "\n"

    def test_external_adapter_uses_frozen_argv_and_persists_provenance_before_mocked_process(self):
        with tempfile.TemporaryDirectory(prefix="phase6-adapter-test-") as temp_dir:
            result_path = self._temporary_result(temp_dir)
            capture_dir = Path(temp_dir) / "captures"
            fake_executable = str(Path(temp_dir) / "private" / "codex.exe")
            call_count = 0

            def mocked_run(command, **kwargs):
                nonlocal call_count
                call_count += 1
                persisted = json.loads(result_path.read_text(encoding="utf-8"))
                self.assertEqual(call_count, persisted["attempted_trial_count"])
                trial = next(row for row in persisted["formal_trials"].values() if row["execution_status"] == "INVOCATION_IN_PROGRESS")
                self.assertEqual("INVOCATION_IN_PROGRESS", trial["execution_status"])
                self.assertTrue(trial["argv_persisted_before_process_outcome"])
                self.assertEqual(list(FROZEN_SANITIZED_ARGV), trial["sanitized_argv"])
                self.assertEqual("PHASE5_OFFICIAL_HOST_INVOCATION_V1", trial["invocation_spec_id"])
                self.assertEqual(EXECUTION_HARNESS_VERSION, trial["execution_harness_version"])
                self.assertEqual("c00a30408ea66d1e595a4f2452c9dea3e52ed6e9fb5cfad33a728045fe83c8da", trial["sanitized_argv_sha256"])
                self.assertEqual([fake_executable, *FROZEN_SANITIZED_ARGV[1:]], command)
                self.assertNotIn("--ask-for-approval", command)
                self.assertFalse(kwargs["shell"])
                self.assertIn(kwargs["input"], {row["model_visible_text"] for row in self.packets["packets"]})
                cwd = Path(kwargs["cwd"])
                self.assertTrue(cwd.is_dir())
                self.assertEqual([], list(cwd.iterdir()))
                self.assertNotEqual(ROOT.resolve(), cwd.resolve())
                self.assertNotIn(ROOT.resolve(), cwd.resolve().parents)
                self.assertTrue(Path(kwargs["env"].get("CODEX_HOME", "")))
                return SimpleNamespace(stdout=self._host_jsonl(f"mock-thread-{call_count}"), stderr="", returncode=0)

            with patch.dict(os.environ, {CONTROLLER_ENV: FORMAL_CONTROLLER}), \
                 patch("scripts.eval.run_presence_regression_phase6.shutil.which", return_value=fake_executable), \
                 patch("scripts.eval.run_behavioral_phase5.subprocess.run", side_effect=mocked_run):
                completed = run_formal_matrix(results_path=result_path, capture_dir=capture_dir)

            self.assertEqual("FORMAL_MATRIX_COMPLETE", completed["status"])
            self.assertEqual("CONSUMED", completed["formal_execution_authorization_state"])
            self.assertFalse(completed["currently_execution_authorized"])
            self.assertEqual(24, call_count)
            self.assertEqual(24, completed["formal_trial_count"])
            self.assertEqual(0, completed["nexus_model_receipt_count"])
            self.assertTrue(all(row["subprocess_shell"] is False for row in completed["formal_trials"].values()))
            self.assertTrue(all(row["formal_trial_counted"] for row in completed["formal_trials"].values()))
            persisted_json = result_path.read_text(encoding="utf-8")
            self.assertNotIn(fake_executable, persisted_json)
            self.assertNotIn(str(capture_dir.resolve()), persisted_json)
            self.assertEqual("PRIVATE_EXTERNAL_CAPTURE_NOT_COMMITTED", completed["raw_capture_location"])
            with patch.dict(os.environ, {CONTROLLER_ENV: FORMAL_CONTROLLER}), \
                 patch("scripts.eval.run_presence_regression_phase6.shutil.which", return_value="codex"), \
                 patch("scripts.eval.run_behavioral_phase5.subprocess.run") as process:
                with self.assertRaises(SystemExit):
                    run_formal_matrix(results_path=result_path, capture_dir=capture_dir)
                process.assert_not_called()

    def test_external_adapter_stops_on_first_mocked_protocol_failure(self):
        cases = (
            ("PRE_MODEL_HOST_INVOCATION_FAILURE", SimpleNamespace(stdout="", stderr="parser failure", returncode=2)),
            ("INCOMPLETE_HOST_EXECUTION", SimpleNamespace(stdout='{"type":"thread.started","thread_id":"incomplete"}\n', stderr="", returncode=0)),
            ("INCOMPLETE_HOST_EXECUTION", SimpleNamespace(stdout='{"type":"thread.started","thread_id":"empty-output"}\n{"type":"item.completed","item":{"id":"empty","type":"agent_message","text":""}}\n{"type":"turn.completed","usage":{}}\n', stderr="", returncode=0)),
            ("CONTAMINATED_BY_TOOL_USE", SimpleNamespace(stdout=self._host_jsonl("tool-thread", tool=True), stderr="", returncode=0)),
        )
        for expected_status, response in cases:
            with self.subTest(expected_status=expected_status), tempfile.TemporaryDirectory(prefix="phase6-fail-closed-") as temp_dir:
                result_path = self._temporary_result(temp_dir)
                capture_dir = Path(temp_dir) / "captures"
                with patch.dict(os.environ, {CONTROLLER_ENV: FORMAL_CONTROLLER}), \
                     patch("scripts.eval.run_presence_regression_phase6.shutil.which", return_value="codex"), \
                     patch("scripts.eval.run_behavioral_phase5.subprocess.run", return_value=response) as process:
                    stopped = run_formal_matrix(results_path=result_path, capture_dir=capture_dir)
                self.assertEqual("STOPPED_ON_PROTOCOL_CONDITION", stopped["status"])
                self.assertEqual(expected_status, stopped["blocking_condition"])
                self.assertEqual(1, stopped["attempted_trial_count"])
                self.assertEqual(0, stopped["formal_trial_count"])
                self.assertEqual(1, process.call_count)

        with tempfile.TemporaryDirectory(prefix="phase6-timeout-") as temp_dir:
            result_path = self._temporary_result(temp_dir)
            capture_dir = Path(temp_dir) / "captures"
            with patch.dict(os.environ, {CONTROLLER_ENV: FORMAL_CONTROLLER}), \
                 patch("scripts.eval.run_presence_regression_phase6.shutil.which", return_value="codex"), \
                 patch("scripts.eval.run_behavioral_phase5.subprocess.run", side_effect=subprocess.TimeoutExpired("codex", 1)) as process:
                stopped = run_formal_matrix(results_path=result_path, capture_dir=capture_dir)
            self.assertEqual("TIMEOUT", stopped["blocking_condition"])
            self.assertEqual(1, stopped["attempted_trial_count"])
            self.assertEqual(0, stopped["formal_trial_count"])
            self.assertEqual(1, process.call_count)

    def test_external_adapter_stops_when_mock_reuses_thread_id(self):
        with tempfile.TemporaryDirectory(prefix="phase6-reused-thread-") as temp_dir:
            result_path = self._temporary_result(temp_dir)
            capture_dir = Path(temp_dir) / "captures"
            response = self._host_jsonl("same-thread")
            with patch.dict(os.environ, {CONTROLLER_ENV: FORMAL_CONTROLLER}), \
                 patch("scripts.eval.run_presence_regression_phase6.shutil.which", return_value="codex"), \
                 patch("scripts.eval.run_behavioral_phase5.subprocess.run", return_value=SimpleNamespace(stdout=response, stderr="", returncode=0)) as process:
                stopped = run_formal_matrix(results_path=result_path, capture_dir=capture_dir)
            self.assertEqual("FRESH_THREAD_ID_REUSED", stopped["blocking_condition"])
            self.assertEqual(2, stopped["attempted_trial_count"])
            self.assertEqual(1, stopped["formal_trial_count"])
            self.assertEqual(1, stopped["unique_thread_id_count"])
            self.assertEqual(2, process.call_count)


if __name__ == "__main__":
    unittest.main()
