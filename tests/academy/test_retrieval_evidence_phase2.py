import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "eval" / "academy" / "retrieval-evidence-phase2-fixture.json"
RESULT = ROOT / "eval" / "academy" / "results" / "retrieval-evidence-phase2.json"
RUNNER_PATH = ROOT / "scripts" / "eval" / "run_retrieval_evidence_phase2.py"
SPEC = importlib.util.spec_from_file_location("academy_phase2_runner", RUNNER_PATH)
RUNNER = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(RUNNER)


class RetrievalEvidencePhase2Tests(unittest.TestCase):
    def test_fixture_is_fixed_and_ground_truth_is_separate(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertTrue(fixture["frozen_before_evaluation"])
        self.assertTrue(fixture["synthetic_public_only"])
        self.assertEqual(len(fixture["items"]), 18)
        self.assertEqual(len(fixture["queries"]), 14)
        self.assertEqual(len(fixture["query_robustness_probes"]), 9)
        self.assertEqual(len({item["id"] for item in fixture["items"]}), 18)
        self.assertTrue(all(query["relevant_ids"] is not None for query in fixture["queries"]))
        self.assertEqual(fixture["truth_rules"]["unresolved_conflict"], "UNKNOWN")
        self.assertEqual(fixture["truth_rules"]["insufficient_evidence"], "INCONCLUSIVE")

    def test_metrics_use_fixed_cutoffs_and_dont_inject_expected_ids(self):
        metrics = RUNNER.retrieval_metrics(["a", "x", "b"], {"a", "b"})
        self.assertEqual(metrics["precision@1"], 1.0)
        self.assertAlmostEqual(metrics["precision@3"], 2 / 3)
        self.assertEqual(metrics["recall@1"], 0.5)
        self.assertEqual(metrics["recall@3"], 1.0)
        self.assertEqual(metrics["mrr"], 1.0)
        self.assertIsNone(RUNNER.retrieval_metrics([], set())["recall@3"])

    def test_fresh_synthetic_api_run_preserves_truth_boundaries_and_proposed_pack_only(self):
        with tempfile.TemporaryDirectory(prefix="academy-phase2-test-") as temp:
            base = Path(temp)
            result = RUNNER.run(data_root=base / "data", journal=base / "journal.jsonl",
                results_path=base / "result.json")
        self.assertEqual(result["corpus"]["item_count"], 18)
        self.assertEqual(result["corpus"]["query_count"], 14)
        self.assertEqual(result["model_invocation_count"], 0)
        self.assertEqual(result["model_execution_receipts"], [])
        self.assertEqual(result["runtime_persistence"]["final_persisted_counts"]["model_runs"], 0)
        self.assertEqual(result["runtime_persistence"]["final_persisted_counts"]["runs"], 2)
        self.assertTrue(result["no_expected_id_fallback"])
        self.assertTrue(result["eligibility_exclusions"]["quarantined_excluded_from_admitted"])
        self.assertTrue(result["eligibility_exclusions"]["expired_excluded_from_raw_and_admitted"])
        self.assertTrue(result["eligibility_exclusions"]["classification_incompatible_excluded"])
        self.assertTrue(result["eligibility_exclusions"]["authority_inaccessible_excluded"])
        self.assertEqual(result["evidence_qualification"]["D_CONFLICTING_SYNTHETIC_SOURCES"]["truth_state"], "UNKNOWN")
        self.assertEqual(result["evidence_qualification"]["E_INSUFFICIENT_EVIDENCE"]["verdict"], "INCONCLUSIVE")
        t1 = result["verification_facts"]["old-quarantined-amber"]
        self.assertEqual(t1["verdict"], "PASS")
        self.assertFalse(t1["semantic_truth_established_by_t1"])
        self.assertEqual(result["packs"]["R1_TOP_K_MINIMAL"]["record_type"], "PROPOSED_GROUNDING_PACK_NOT_EXPOSED")
        self.assertEqual(result["packs"]["R1_TOP_K_MINIMAL"]["model_exposure"], "NOT_EXECUTED")
        self.assertLessEqual(set(result["packs"]["R1_TOP_K_MINIMAL"]["object_refs"]),
            set(result["packs"]["R2_BROAD_ELIGIBLE"]["object_refs"]))
        self.assertTrue(result["replay"]["no_duplicate_mutation"])
        self.assertEqual(result["replay"]["same_command_changed_object"], "COMMAND_CONFLICT")

    def test_runner_refuses_nonempty_disposable_root(self):
        with tempfile.TemporaryDirectory(prefix="academy-phase2-nonempty-") as temp:
            base = Path(temp)
            root = base / "data"
            root.mkdir()
            sentinel = root / "preserve.txt"
            sentinel.write_text("do not overwrite", encoding="utf-8")
            with self.assertRaises(SystemExit):
                RUNNER.run(data_root=root, journal=base / "journal.jsonl", results_path=base / "result.json")
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "do not overwrite")

    def test_committed_observation_has_only_sanitized_live_receipt_metadata(self):
        result = json.loads(RESULT.read_text(encoding="utf-8"))
        self.assertEqual(result["real_public_search"]["receipt_count"], 1)
        self.assertEqual(result["real_public_search"]["status"], "RECORDED_THROUGH_HOSTED_EVIDENCE_API")
        self.assertEqual(result["real_public_search"]["execution_source"], "CODEX_HOST_DECLARED")
        self.assertEqual(result["real_public_search"]["provider_model_request_id"], "UNAVAILABLE")
        self.assertTrue(all(not row["raw_url_and_excerpt_in_committed_result"]
            for row in result["real_public_search"]["receipts"]))
        self.assertEqual(result["host_configuration_observation"]["tool_chat_memory_generation"],
            "ON_FORCED / USER_NOT_CONTROLLABLE")
        self.assertEqual(result["host_configuration_observation"]["cross_session_effect_while_primary_memory_off"],
            "NOT_INDEPENDENTLY_VERIFIED")
        self.assertEqual(result["policy_observation"]["base_policy"],
            "REPOSITORY_DEFAULT_FAIL_CLOSED_POLICY")
        self.assertEqual(result["policy_observation"]["test_only_difference"],
            "trust_anchors replaced with academy-human-root")
        self.assertFalse(result["policy_observation"]["other_policy_fields_changed"])
        self.assertFalse(result["policy_observation"]["persistent_default_policy_modified"])
        self.assertEqual(result["query_robustness"]["normalization_candidate"], "FORMED / ACADEMY-ONLY")
        probes = {row["probe_id"]: row for row in result["query_robustness"]["probes"]}
        self.assertEqual(probes["hyphen"]["Q0"]["status"], "ERROR")
        self.assertEqual(probes["hyphen"]["Q1"]["status"], "SUCCESS")


if __name__ == "__main__":
    unittest.main()
