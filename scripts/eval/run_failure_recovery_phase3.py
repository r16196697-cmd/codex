"""Run a frozen Academy Phase 3 matrix through existing synthetic Core tests."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
FIXTURE = REPO_ROOT / "eval" / "academy" / "failure-recovery-phase3-fixture.json"
RESULT = REPO_ROOT / "eval" / "academy" / "results" / "failure-recovery-phase3.json"


class EvidenceResult(unittest.TestResult):
    def __init__(self) -> None:
        super().__init__()
        self.observations: list[dict[str, Any]] = []
        self.current: dict[unittest.case.TestCase, dict[str, Any]] = {}

    def startTest(self, test: unittest.case.TestCase) -> None:
        row = {"test_id": test.id(), "status": "RUNNING"}
        self.observations.append(row)
        self.current[test] = row
        super().startTest(test)

    def addSuccess(self, test: unittest.case.TestCase) -> None:
        self.current[test]["status"] = "PASS"
        super().addSuccess(test)

    def addFailure(self, test: unittest.case.TestCase, err: Any) -> None:
        self.current[test].update(status="FAIL", failure_type=err[0].__name__)
        super().addFailure(test, err)

    def addError(self, test: unittest.case.TestCase, err: Any) -> None:
        self.current[test].update(status="ERROR", failure_type=err[0].__name__)
        super().addError(test, err)

    def addSkip(self, test: unittest.case.TestCase, reason: str) -> None:
        self.current[test].update(status="SKIP", skip_reason=reason)
        super().addSkip(test, reason)


def _flatten(suite: unittest.TestSuite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        else:
            yield item


def run(*, fixture_path: Path = FIXTURE, output_path: Path = RESULT,
        scratch_root: Path | None = None) -> dict[str, Any]:
    fixture_bytes = fixture_path.read_bytes()
    fixture = json.loads(fixture_bytes)
    if not fixture.get("frozen_before_evaluation") or not fixture.get("synthetic_only"):
        raise SystemExit("Phase 3 failure matrix must be frozen and synthetic-only")
    case_ids = [row["case_id"] for row in fixture["cases"]]
    if len(case_ids) != len(set(case_ids)):
        raise SystemExit("Phase 3 case IDs must be unique")
    test_ids = list(dict.fromkeys(test_id for case in fixture["cases"] for test_id in case["test_ids"]))
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite(loader.loadTestsFromName(test_id) for test_id in test_ids)
    if len(list(_flatten(suite))) != len(test_ids):
        raise SystemExit("A frozen integration test could not be loaded")

    if scratch_root is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        scratch_root = Path.home() / "Documents" / "ChatGPT" / f"nexus-academy-phase3-{stamp}"
    scratch_root = scratch_root.resolve()
    if scratch_root.exists():
        raise SystemExit(f"Refusing to reuse an existing Phase 3 scratch root: {scratch_root}")
    scratch_root.mkdir(parents=True, exist_ok=False)
    previous_tempdir = tempfile.tempdir
    tempfile.tempdir = str(scratch_root)
    try:
        result = EvidenceResult()
        suite.run(result)
    finally:
        tempfile.tempdir = previous_tempdir

    observed_rows = result.observations
    if result.errors or result.failures:
        for test, formatted_traceback in [*result.errors, *result.failures]:
            print(f"Evidence test {test.id()} failed:", file=sys.stderr)
            print(formatted_traceback, file=sys.stderr)
    if result.testsRun != len(test_ids) or len(observed_rows) != len(test_ids):
        raise RuntimeError(f"Failure matrix executed {result.testsRun}/{len(test_ids)} evidence tests; observed {len(observed_rows)} results")
    observed = {row["test_id"]: row for row in observed_rows}
    case_results = []
    for case in fixture["cases"]:
        linked = [observed.get(test_id, {"test_id": test_id, "status": "NOT_RUN"})
                  for test_id in case["test_ids"]]
        status = "PASS" if all(row["status"] == "PASS" for row in linked) else (
            "INCONCLUSIVE" if any(row["status"] in {"SKIP", "NOT_RUN"} for row in linked) else "BLOCKER")
        case_results.append({"case_id": case["case_id"], "family": case["family"],
                             "status": status, "evidence_tests": linked})

    passed_ids = {row["test_id"] for row in observed.values() if row["status"] == "PASS"}
    fail_closed = [row for row in fixture["cases"]
                   if row["expected_denied_action"].strip().lower() not in {"", "none"}]
    fail_closed_pass = sum(all(test_id in passed_ids for test_id in row["test_ids"]) for row in fail_closed)
    hard_evidence_ids = {
        "response_loss": "tests.academy.test_failure_recovery_phase3.FailureRecoveryPhase3Tests.test_committed_object_response_loss_replays_once_after_reopen",
        "effect": "tests.integration.test_deterministic_runtime.DeterministicRuntimeTests.test_unknown_effect_is_reconciled_without_retry_and_compensation_is_independent",
        "purge": "tests.integration.test_memory_purge.MemoryPurgeTests.test_proof_purge_redacts_governed_derivatives_and_replay_results",
        "restore": "tests.integration.test_memory_purge.MemoryPurgeTests.test_old_snapshot_recovery_replays_purge_ledger_before_normal",
        "revoke": "tests.integration.test_authority_budget.AuthorityBudgetTests.test_principal_revocation_invalidates_every_chain",
        "journal": "tests.integration.test_object_store.ObjectStoreTests.test_ordinary_startup_fails_closed_on_journal_corruption",
        "stale_journal": "tests.integration.test_object_store.ObjectStoreTests.test_ordinary_startup_forces_recovery_when_external_journal_is_ahead",
        "journal_identity": "tests.academy.test_failure_recovery_phase3.FailureRecoveryPhase3Tests.test_journal_identity_mismatch_forces_recovery",
        "purge_replay": "tests.integration.test_memory_purge.MemoryPurgeTests.test_purge_backup_restore_replays_external_ledger_without_resurrection",
        "fake_effect_matrix": "tests.academy.test_failure_recovery_phase3.FailureRecoveryPhase3Tests.test_fake_dispatch_outcomes_reconcile_without_blind_retry",
    }
    output = {
        "schema_version": 1,
        "fixture_id": fixture["fixture_id"],
        "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
        "execution_mode": "SYNTHETIC_EXISTING_CORE_INTEGRATION_TESTS",
        "evidence_class": "REGRESSION_BACKED_OPERATIONAL_QUALIFICATION",
        "qualification_limits": [
            "NOT_INDEPENDENT_BLACK_BOX_RESILIENCE_CAMPAIGN",
            "NOT_REAL_OS_CRASH_QUALIFICATION",
            "NOT_PRODUCTION_RECOVERY_QUALIFICATION",
            "NOT_PHYSICAL_DURABILITY_QUALIFICATION",
        ],
        "failure_injection_semantics": "SIMULATED_CRASH_BOUNDARY; temporary test roots and explicit close/reopen/fault hooks, not OS power-loss",
        "case_count": len(case_results),
        "unique_evidence_test_count": result.testsRun,
        "evidence_test_status_counts": {status: sum(row["status"] == status for row in observed_rows)
                                        for status in ("PASS", "FAIL", "ERROR", "SKIP")},
        "passed_case_count": sum(row["status"] == "PASS" for row in case_results),
        "blocker_case_count": sum(row["status"] == "BLOCKER" for row in case_results),
        "inconclusive_case_count": sum(row["status"] == "INCONCLUSIVE" for row in case_results),
        "cases_with_denial_expectation": len(fail_closed),
        "mapped_denial_cases_passed": fail_closed_pass,
        "mapped_cases_passed": sum(row["status"] == "PASS" for row in case_results),
        "duplicate_synthetic_external_effect_count": 0 if hard_evidence_ids["fake_effect_matrix"] in passed_ids and hard_evidence_ids["effect"] in passed_ids else None,
        "duplicate_mutation_count": 0 if hard_evidence_ids["response_loss"] in passed_ids and hard_evidence_ids["purge"] in passed_ids and hard_evidence_ids["effect"] in passed_ids else None,
        "purged_identifier_reexposure_count": 0 if hard_evidence_ids["purge"] in passed_ids and hard_evidence_ids["purge_replay"] in passed_ids else None,
        "unauthorized_new_mutation_after_revocation_count": 0 if hard_evidence_ids["revoke"] in passed_ids else None,
        "unexpected_normal_entry_count": 0 if all(hard_evidence_ids[key] in passed_ids for key in ("journal", "stale_journal", "journal_identity")) else None,
        "detected_corruption_case_count": sum(row["status"] == "PASS" and row["family"] == "CONTROL_PLANE_CORRUPTION" for row in case_results),
        "undetected_corruption_case_count": sum(row["status"] == "BLOCKER" and row["family"] == "CONTROL_PLANE_CORRUPTION" for row in case_results),
        "physical_durability": "NOT FULLY QUALIFIED; Windows run does not execute POSIX directory-fsync path or power-cut tests",
        "host_configuration_observation": {
            "chatgpt_primary_memory": "OFF",
            "tool_chat_memory_generation": "ON_FORCED / USER_NOT_CONTROLLABLE",
            "cross_session_effect_while_primary_memory_off": "NOT_INDEPENDENTLY_VERIFIED",
            "custom_instructions": "ON / UNCHANGED",
            "global_agents_md": "UNCHANGED",
            "project_experience_curator": "UNCHANGED / DISCOVERED / UNEVALUATED",
            "model_behavioral_evaluation": "NOT RUN; Host native Memory not an input to recovery qualification",
        },
        "host_configuration_changed": False,
        "cases": case_results,
        "temporary_data_policy": "isolated per-test TemporaryDirectory under a fresh Academy scratch root; no DB/journal committed",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--output", type=Path, default=RESULT)
    parser.add_argument("--scratch-root", type=Path, default=None)
    args = parser.parse_args()
    output = run(fixture_path=args.fixture, output_path=args.output, scratch_root=args.scratch_root)
    print(json.dumps({key: output[key] for key in (
        "fixture_sha256", "case_count", "unique_evidence_test_count", "mapped_cases_passed",
        "cases_with_denial_expectation", "mapped_denial_cases_passed", "blocker_case_count",
        "inconclusive_case_count",
        "duplicate_synthetic_external_effect_count", "duplicate_mutation_count",
        "purged_identifier_reexposure_count", "unexpected_normal_entry_count")}, indent=2))
    return 0 if output["blocker_case_count"] == 0 and output["inconclusive_case_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
