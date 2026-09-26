from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "eval" / "academy" / "fixtures" / "routing-phase4-fixture.json"
RESULT = ROOT / "eval" / "academy" / "results" / "routing-phase4.json"


class RoutingPhase4ProtocolTests(unittest.TestCase):
    def test_fixed_fixture_and_result_are_bound_and_truthful(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        result = json.loads(RESULT.read_text(encoding="utf-8"))
        raw = FIXTURE.read_bytes()
        self.assertTrue(fixture["frozen_before_evaluation"])
        self.assertTrue(fixture["synthetic_only"])
        self.assertEqual(result["fixture_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(len(fixture["cases"]), result["case_count"])
        self.assertEqual(result["hosted_model_identity"], "UNAVAILABLE")
        self.assertEqual(result["provider_cost"], "UNAVAILABLE_FOR_REAL_MODEL_ROUTING")
        self.assertEqual(result["fake_codex_host_declared_receipt_count"], 0)
        self.assertTrue(result["not_a_real_provider_or_model_evaluation"])
        self.assertEqual(result["blocker_case_count"], 0)

    def test_unrepresented_and_untested_cases_are_not_counted_as_supported(self):
        result = json.loads(RESULT.read_text(encoding="utf-8"))
        cases = {row["case_id"]: row for row in result["cases"]}
        self.assertEqual(cases["R04_INCONCLUSIVE_ATTEMPT_THEN_ESCALATION"]["observed_status"], "NOT_REPRESENTABLE")
        self.assertEqual(cases["R06_CAPABILITY_MISMATCH_FALLBACK"]["observed_status"], "NOT_REPRESENTABLE")
        self.assertEqual(cases["R08_NETWORK_FORBIDDEN"]["observed_status"], "NOT_TESTED")
        self.assertEqual(cases["R09_MODALITY_MISMATCH"]["observed_status"], "NOT_TESTED")

    def test_phase3_mapping_metrics_are_not_presented_as_independent_runs(self):
        phase3 = json.loads((ROOT / "eval" / "academy" / "results" / "failure-recovery-phase3.json").read_text(encoding="utf-8"))
        self.assertEqual(phase3["case_count"], 27)
        self.assertEqual(phase3["unique_evidence_test_count"], 23)
        self.assertEqual(phase3["mapped_cases_passed"], 27)
        self.assertEqual(phase3["cases_with_denial_expectation"], 27)
        self.assertEqual(phase3["mapped_denial_cases_passed"], 27)
        self.assertEqual(phase3["evidence_class"], "REGRESSION_BACKED_OPERATIONAL_QUALIFICATION")
        self.assertNotIn("recovery_success_count", phase3)
        self.assertNotIn("observed_fail_closed_count", phase3)


if __name__ == "__main__":
    unittest.main()
