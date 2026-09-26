"""Evaluate the frozen synthetic routing matrix against existing regressions."""
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

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "eval" / "academy" / "fixtures" / "routing-phase4-fixture.json"
RESULT = ROOT / "eval" / "academy" / "results" / "routing-phase4.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class EvidenceResult(unittest.TestResult):
    def __init__(self) -> None:
        super().__init__()
        self.rows: dict[str, dict[str, Any]] = {}

    def startTest(self, test: unittest.case.TestCase) -> None:
        self.rows[test.id()] = {"test_id": test.id(), "status": "RUNNING"}
        super().startTest(test)

    def addSuccess(self, test: unittest.case.TestCase) -> None:
        self.rows[test.id()]["status"] = "PASS"
        super().addSuccess(test)

    def addFailure(self, test: unittest.case.TestCase, err: Any) -> None:
        self.rows[test.id()].update(status="FAIL", failure_type=err[0].__name__)
        super().addFailure(test, err)

    def addError(self, test: unittest.case.TestCase, err: Any) -> None:
        self.rows[test.id()].update(status="ERROR", failure_type=err[0].__name__)
        super().addError(test, err)

    def addSkip(self, test: unittest.case.TestCase, reason: str) -> None:
        self.rows[test.id()].update(status="SKIP", reason=reason)
        super().addSkip(test, reason)


def run(*, fixture_path: Path = FIXTURE, output_path: Path = RESULT,
        scratch_root: Path | None = None) -> dict[str, Any]:
    raw = fixture_path.read_bytes()
    fixture = json.loads(raw)
    if fixture.get("frozen_before_evaluation") is not True or fixture.get("synthetic_only") is not True:
        raise SystemExit("Routing fixture must be frozen and synthetic-only")
    ids = [case["case_id"] for case in fixture["cases"]]
    if len(ids) != len(set(ids)):
        raise SystemExit("Routing case IDs must be unique")
    test_ids = list(dict.fromkeys(test_id for case in fixture["cases"] for test_id in case["test_ids"]))
    tests = [unittest.defaultTestLoader.loadTestsFromName(test_id) for test_id in test_ids]
    if any(isinstance(test, unittest.loader._FailedTest) for test in tests):
        raise SystemExit("A frozen routing evidence test could not be loaded")
    suite = unittest.TestSuite(tests)
    if scratch_root is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        scratch_root = Path(tempfile.gettempdir()) / f"nexus-academy-phase4-{stamp}"
    scratch_root = scratch_root.resolve()
    if scratch_root.exists():
        raise SystemExit(f"Refusing to reuse an existing Phase 4 scratch root: {scratch_root}")
    scratch_root.mkdir(parents=True, exist_ok=False)
    prior_tempdir = tempfile.tempdir
    tempfile.tempdir = str(scratch_root)
    try:
        evidence = EvidenceResult()
        suite.run(evidence)
    finally:
        tempfile.tempdir = prior_tempdir
    if evidence.failures or evidence.errors:
        for test, trace in [*evidence.failures, *evidence.errors]:
            print(f"Routing evidence failed: {test.id()}\n{trace}", file=sys.stderr)
    if evidence.testsRun != len(test_ids):
        raise RuntimeError(f"Ran {evidence.testsRun} of {len(test_ids)} unique evidence tests")

    cases = []
    for case in fixture["cases"]:
        linked = [evidence.rows.get(test_id, {"test_id": test_id, "status": "NOT_RUN"})
                  for test_id in case["test_ids"]]
        declared = case.get("qualification_status", "QUALIFIED_BY_MAPPED_REGRESSION")
        if declared == "NOT_REPRESENTABLE":
            status = "NOT_REPRESENTABLE"
        elif declared == "NOT_TESTED":
            status = "NOT_TESTED"
        else:
            status = "SUPPORTED_BY_MAPPED_REGRESSION" if linked and all(
                row["status"] == "PASS" for row in linked) else "BLOCKER"
        cases.append({"case_id": case["case_id"], "category": case["category"],
                      "qualification_status": declared, "observed_status": status,
                      "evidence_tests": linked})
    test_statuses = [row["status"] for row in evidence.rows.values()]
    output = {
        "schema_version": 1,
        "fixture_id": fixture["fixture_id"],
        "fixture_sha256": hashlib.sha256(raw).hexdigest(),
        "evidence_class": "SYNTHETIC_DETERMINISTIC_ROUTING_REGRESSION_QUALIFICATION",
        "not_a_real_provider_or_model_evaluation": True,
        "real_provider_selection": "NOT_TESTED / NOT IMPLEMENTED",
        "hosted_model_identity": "UNAVAILABLE",
        "provider_cost": "UNAVAILABLE_FOR_REAL_MODEL_ROUTING",
        "fake_codex_host_declared_receipt_count": 0,
        "case_count": len(cases),
        "unique_evidence_test_count": evidence.testsRun,
        "evidence_status_counts": {status: test_statuses.count(status)
                                   for status in ("PASS", "FAIL", "ERROR", "SKIP")},
        "supported_mapped_case_count": sum(row["observed_status"] == "SUPPORTED_BY_MAPPED_REGRESSION" for row in cases),
        "not_representable_case_count": sum(row["observed_status"] == "NOT_REPRESENTABLE" for row in cases),
        "not_tested_case_count": sum(row["observed_status"] == "NOT_TESTED" for row in cases),
        "blocker_case_count": sum(row["observed_status"] == "BLOCKER" for row in cases),
        "host_configuration_changed": False,
        "temporary_data_policy": "Synthetic tests use disposable temporary roots; no DB, journal, snapshot, or host inventory is committed.",
        "cases": cases,
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
    result = run(fixture_path=args.fixture, output_path=args.output, scratch_root=args.scratch_root)
    print(json.dumps({key: result[key] for key in (
        "fixture_sha256", "case_count", "unique_evidence_test_count",
        "supported_mapped_case_count", "not_representable_case_count",
        "not_tested_case_count", "blocker_case_count", "fake_codex_host_declared_receipt_count")}, indent=2))
    return 0 if result["blocker_case_count"] == 0 and not any(
        count for status, count in result["evidence_status_counts"].items() if status in {"FAIL", "ERROR", "SKIP"}) else 1


if __name__ == "__main__":
    raise SystemExit(main())
