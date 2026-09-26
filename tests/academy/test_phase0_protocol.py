import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "eval" / "academy" / "results" / "context-exposure-phase0.json"


class AcademyPhase0ProtocolTests(unittest.TestCase):
    def test_protocol_marks_host_model_instrumentation_as_gap(self):
        protocol = (ROOT / "eval" / "academy" / "context-exposure-phase0.md").read_text(encoding="utf-8")
        self.assertIn("NOT IMPLEMENTED / CANDIDATE", protocol)
        self.assertIn("ACADEMY GAP", protocol)
        self.assertIn("does not invoke or isolate a model", protocol)

    def test_results_do_not_invent_host_telemetry_or_identity(self):
        result = json.loads(RESULT.read_text(encoding="utf-8"))
        self.assertEqual(result["schema_version"], 22)
        self.assertEqual(result["phase0_structural_status"], "SUPPORTED")
        self.assertEqual(result["model_task_success"], "UNAVAILABLE / NOT YET TESTED")
        self.assertEqual(result["journal"]["path"], "DISPOSABLE_EXTERNAL_PATH_NOT_COMMITTED")
        for metric in ("input_tokens", "output_tokens", "provider_cost", "wall_clock"):
            self.assertEqual(result["host_telemetry"][metric], "UNAVAILABLE")
        self.assertEqual(result["model_invocation_count"], 0)
        self.assertEqual(result["model_run_count"], 0)
        self.assertEqual(result["model_execution_receipts"], [])
        self.assertTrue(all(row["model_invocation"] == "NOT_EXECUTED" for row in result["proposed_model_context_sets"].values()))
        self.assertFalse(any("execution_source" in row for row in result["proposed_model_context_sets"].values()))

    def test_exposure_conditions_are_safe_and_distinct(self):
        result = json.loads(RESULT.read_text(encoding="utf-8"))
        conditions = result["conditions"]
        self.assertEqual(conditions["C0_NONE"]["exposed_object_count"], 0)
        self.assertGreater(conditions["C1_MINIMAL_RELEVANT"]["exposed_object_count"], 0)
        self.assertGreater(conditions["C2_BROAD_SAFE"]["exposed_object_count"], conditions["C1_MINIMAL_RELEVANT"]["exposed_object_count"])
        self.assertTrue(result["retrieval"]["quarantined_ids_excluded"])
        self.assertEqual(conditions["P3_INVOCATION"]["exposed_object_count"], 0)


if __name__ == "__main__":
    unittest.main()
