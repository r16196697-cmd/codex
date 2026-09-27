from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class BootstrapSynthesisTests(unittest.TestCase):
    def test_candidate_ledger_never_promotes_and_preserves_known_gaps(self):
        ledger = json.loads((ROOT / "eval/academy/results/bootstrap-academy-candidates.json").read_text(encoding="utf-8"))
        self.assertTrue(all(item["promotion_allowed"] is False for item in ledger["candidates"]))
        self.assertEqual(ledger["capability_certification"]["status"], "NOT STARTED")
        self.assertEqual(ledger["capability_certification"]["discovered_skill_count"], 136)
        self.assertEqual(ledger["capability_certification"]["all_discovered_skills_lifecycle"], "DISCOVERED / UNEVALUATED")
        self.assertEqual(ledger["capability_certification"]["project_experience_curator"], "DISCOVERED / UNEVALUATED")
        self.assertEqual(ledger["shadow"], "NOT STARTED")
        self.assertEqual(ledger["production_qualification"], "NOT STARTED")

    def test_phase_statuses_and_shadow_gate_keep_behavioral_gaps_open(self):
        ledger = json.loads((ROOT / "eval/academy/results/bootstrap-academy-candidates.json").read_text(encoding="utf-8"))
        statuses = {row["phase"]: row["status"] for row in ledger["phases"]}
        self.assertEqual(statuses, {"0":"CLOSED / ACCEPTED", "1":"CLOSED / ACCEPTED", "2":"CLOSED / ACCEPTED", "3":"CLOSED / ACCEPTED", "4":"CLOSED / ACCEPTED", "5":"CLOSED / ACCEPTED", "6":"IN PROGRESS / FORMAL EXECUTION STOPPED — EXTERNAL REVIEW PENDING"})
        gates = {row["gate"]: row["status"] for row in ledger["shadow_entry_gate_candidate"]}
        self.assertEqual(gates["G1_EXPLICIT_EXPERIMENT_PACKET_DELIVERY"], "SUPPORTED_BY_FORMAL_REAL_HOST_MATRIX")
        self.assertEqual(gates["G2_FULL_MODEL_VISIBLE_CONTEXT_PROOF"], "UNAVAILABLE")
        self.assertEqual(gates["H1_SYNTHETIC_CAPABILITY_PRESENCE_BEHAVIOR"], "OBSERVED_IN_REAL_HOST_PILOT")
        self.assertEqual(gates["H2_REAL_SKILL_CAPABILITY_UTILITY"], "NOT TESTED")
        self.assertEqual(gates["I_HOST_MEMORY_CONTAMINATION"], "UNAVAILABLE / NOT INDEPENDENTLY VERIFIED")
        self.assertEqual(gates["J1_HOST_TOTAL_TURN_TOKEN_TELEMETRY"], "SUPPORTED_BY_REAL_HOST")
        self.assertEqual(gates["J2_PROVIDER_DOLLAR_COST"], "UNAVAILABLE")
        self.assertEqual(ledger["query_normalization_decision"]["retrieval_relevance_improvement"], "INSUFFICIENT_EVIDENCE")
        self.assertFalse(ledger["core_semantics_changed"])


if __name__ == "__main__":
    unittest.main()
