"""Validate and score the frozen Phase 6 preregistration without Host access.

This preregistration-only utility deliberately has no Host subprocess path.
An execution adapter may be added only after external review authorizes trials.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PACKETS_PATH = ROOT / "eval/academy/fixtures/presence-regression-phase6-packets.json"
EVALUATOR_PATH = ROOT / "eval/academy/fixtures/presence-regression-phase6-evaluator.json"
RESULT_PATH = ROOT / "eval/academy/results/presence-regression-phase6.json"
LEVELS = ("P0_ABSENT", "P1_METADATA_ONLY", "P2_FULL_INSTRUCTION")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _same_json_shape(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_json_shape(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_json_shape(a, b) for a, b in zip(left, right))
    return True


def score_trial(output: str, expected: dict[str, Any], task_kind: str, presence: str) -> dict[str, Any]:
    """Apply the frozen exact JSON scorer; keep utility and preservation separate."""
    try:
        parsed = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        parsed = None
    valid = isinstance(parsed, dict) and _same_json_shape(parsed, expected)
    exact = bool(valid and parsed == expected)
    if task_kind == "CAPABILITY_RELEVANT":
        utility: bool | str = exact if presence == "P2_FULL_INSTRUCTION" else "NOT_APPLICABLE"
        preservation: bool | str = "NOT_APPLICABLE"
        regression: bool | str = "NOT_APPLICABLE"
    else:
        utility = "NOT_APPLICABLE"
        preservation = exact
        regression = "PRESENCE_REGRESSION_OBSERVED" if presence == "P2_FULL_INSTRUCTION" and not exact else "NOT_OBSERVED"
    return {
        "format_valid": bool(valid),
        "exact_correct": exact,
        "relevant_utility": utility,
        "unrelated_task_preservation": preservation,
        "presence_regression": regression,
    }


def validate_preregistration(
    packets_path: Path = PACKETS_PATH,
    evaluator_path: Path = EVALUATOR_PATH,
    result_path: Path = RESULT_PATH,
) -> dict[str, Any]:
    packet_bytes = packets_path.read_bytes()
    evaluator_bytes = evaluator_path.read_bytes()
    packets = json.loads(packet_bytes.decode("utf-8"))
    evaluator = json.loads(evaluator_bytes.decode("utf-8"))
    result = json.loads(result_path.read_text(encoding="utf-8"))

    rows = packets["packets"]
    packet_ids = [row["trial_id"] for row in rows]
    order = packets["execution_order"]
    mapping = evaluator["trials"]
    if len(packet_ids) != 24 or len(set(packet_ids)) != 24 or set(packet_ids) != set(order):
        raise ValueError("Phase 6 packet IDs must be 24 unique IDs and exactly cover execution order.")
    if set(mapping) != set(packet_ids):
        raise ValueError("Phase 6 evaluator mapping must cover every packet exactly once.")
    order_hash = sha256(("\n".join(order) + "\n").encode("utf-8"))
    if order_hash != packets["execution_order_sha256"] or order_hash != result["execution_order_sha256"]:
        raise ValueError("Phase 6 execution-order hash mismatch.")
    if sha256(packet_bytes) != result["packet_fixture_sha256"] or sha256(evaluator_bytes) != result["evaluator_sha256"]:
        raise ValueError("Phase 6 preregistration artifact hash mismatch.")

    seen: dict[tuple[str, str], list[int]] = {}
    family_ids = {family["family_id"] for family in packets["families"]}
    if len(family_ids) != 4:
        raise ValueError("Phase 6 must use four independent synthetic capability families.")
    positions = {trial_id: index for index, trial_id in enumerate(order)}
    for row in rows:
        trial_id = row["trial_id"]
        text = row["model_visible_text"]
        if sha256(text.encode("utf-8")) != row["packet_sha256"]:
            raise ValueError(f"Packet text hash mismatch for {trial_id}.")
        if row["family_id"] not in family_ids or row["presence"] not in LEVELS:
            raise ValueError(f"Unknown family or presence level for {trial_id}.")
        if any(label in text for label in LEVELS):
            raise ValueError(f"Condition label leaked into model-visible packet {trial_id}.")
        if "expected" in row or trial_id not in mapping:
            raise ValueError(f"Expected/scoring metadata leaked into packet {trial_id}.")
        score_row = mapping[trial_id]
        if (score_row["family_id"], score_row["presence"], score_row["task_kind"]) != (
            row["family_id"], row["presence"], row["task_kind"]
        ):
            raise ValueError(f"Evaluator condition mapping mismatch for {trial_id}.")
        key = (row["family_id"], row["task_kind"])
        seen.setdefault(key, []).append(positions[trial_id])

    expected_pairs = {(family, task) for family in family_ids for task in ("CAPABILITY_RELEVANT", "UNRELATED_CONTROL")}
    if set(seen) != expected_pairs or any(len(indices) != 3 for indices in seen.values()):
        raise ValueError("Every family must have one relevant/control trial at each presence level.")
    for key, indices in seen.items():
        ordered_levels = [next(row["presence"] for row in rows if positions[row["trial_id"]] == index) for index in indices]
        if ordered_levels != list(LEVELS):
            raise ValueError(f"Presence order is not monotonic for {key}.")
    if result["formal_trial_count"] != 0 or result["execution_count"] != 0 or result["nexus_model_receipt_count"] != 0:
        raise ValueError("Preregistration result must not contain formal trials or MODEL receipts.")
    if result["execution_authorized"] or packets["execution_authorized"]:
        raise ValueError("Phase 6 Host execution is not authorized before external review.")
    return {
        "protocol_version": packets["protocol_version"],
        "packet_count": len(rows),
        "family_count": len(family_ids),
        "relevant_control_pair_count": len(family_ids) * 3,
        "execution_order_sha256": order_hash,
        "packet_fixture_sha256": sha256(packet_bytes),
        "evaluator_sha256": sha256(evaluator_bytes),
        "formal_trial_count": 0,
        "nexus_model_receipt_count": 0,
        "host_execution_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the Phase 6 presence-regression preregistration; no Host execution.")
    parser.add_argument("--validate", action="store_true", help="Validate frozen packets, evaluator, order, and result metadata.")
    args = parser.parse_args()
    print(json.dumps(validate_preregistration(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
