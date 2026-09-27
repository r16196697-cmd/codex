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
SKILL_SCREEN_PATH = ROOT / "eval/academy/results/phase6-real-skill-static-screen.json"
LEVELS = ("P0_ABSENT", "P1_METADATA_ONLY", "P2_FULL_INSTRUCTION")
SCREEN_CRITERIA = (
    "requires_secret_or_auth",
    "requires_network",
    "requires_filesystem_mutation",
    "requires_shell_or_system_mutation",
    "deterministic_task_available",
    "narrow_trigger",
    "instruction_boundary_clear",
    "safe_unrelated_control_constructible",
)


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
        if exact:
            regression = "NOT_APPLICABLE_BASELINE" if presence == "P0_ABSENT" else "NOT_OBSERVED"
        elif presence == "P0_ABSENT":
            regression = "CONTROL_BASELINE_FAILURE"
        else:
            regression = "PRESENCE_REGRESSION_OBSERVED"
    return {
        "format_valid": bool(valid),
        "exact_correct": exact,
        "relevant_utility": utility,
        "unrelated_task_preservation": preservation,
        "presence_regression": regression,
    }


def derive_screen_exclusion_reasons(criteria: dict[str, str]) -> list[str]:
    """Return deterministic reasons; UNKNOWN never counts as a safe pass."""
    reasons = []
    for name in SCREEN_CRITERIA:
        value = criteria[name]
        label = name.removeprefix("requires_").upper()
        if value == "TRUE":
            reasons.append(f"REQUIRES_{label}")
        elif value == "UNKNOWN":
            reasons.append(f"UNKNOWN_{label}")
        elif value != "FALSE":
            raise ValueError(f"Invalid static-screen criterion value for {name}: {value}")
    return reasons


def validate_skill_screen(ledger: dict[str, Any]) -> dict[str, Any]:
    rows = ledger.get("rows", [])
    if len(rows) != 136:
        raise ValueError("Static Skill screen must contain 136 artifact rows.")
    expected_counts = {"CODEX_SKILLS": 110, "BUNDLED_PLUGIN_SKILLS": 2, "CURATED_PLUGIN_SKILLS": 24}
    counts = {source: sum(row.get("source_category") == source for row in rows) for source in expected_counts}
    if counts != expected_counts:
        raise ValueError(f"Static Skill screen source counts mismatch: {counts}")
    ids = [row.get("artifact_id") for row in rows]
    if any(not isinstance(item, str) or not item for item in ids) or len(set(ids)) != len(ids):
        raise ValueError("Static Skill screen artifact IDs must be present and unique.")
    selected = 0
    for row in rows:
        digest = row.get("instruction_sha256", "")
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError(f"Invalid instruction SHA-256 for {row.get('artifact_id')}.")
        artifact_id = row["artifact_id"]
        if any(token in artifact_id for token in ("C:\\", "/Users/", "/home/", "\\Users\\")):
            raise ValueError("Absolute local path found in static Skill screen artifact ID.")
        criteria = row.get("criteria", {})
        if set(criteria) != set(SCREEN_CRITERIA):
            raise ValueError(f"Static Skill screen criteria incomplete for {artifact_id}.")
        reasons = derive_screen_exclusion_reasons(criteria)
        eligible = not reasons and all(criteria[name] == "TRUE" for name in SCREEN_CRITERIA[4:]) and all(
            criteria[name] == "FALSE" for name in SCREEN_CRITERIA[:4]
        )
        if row.get("exclusion_reasons") != reasons or row.get("selection_eligible") is not eligible:
            raise ValueError(f"Static Skill screen derived fields mismatch for {artifact_id}.")
        selected += int(eligible)
    if ledger.get("selected_candidate_count") != selected:
        raise ValueError("Static Skill screen selected count must be derived from rows.")
    curator = [row for row in rows if "project-experience-curator" in row["artifact_id"].lower()]
    if len(curator) != 1 or curator[0]["selection_eligible"]:
        raise ValueError("project-experience-curator must be present and not eligible.")
    return {"row_count": len(rows), "source_counts": counts, "selected_candidate_count": selected}


def model_visible_packet_digest(rows: list[dict[str, Any]]) -> str:
    ordered = sorted(rows, key=lambda row: row["trial_id"])
    payload = "\n".join(f"{row['trial_id']}\0{row['model_visible_text']}" for row in ordered) + "\n"
    return sha256(payload.encode("utf-8"))


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
    if packets["protocol_version"] != "PHASE6_PRESENCE_REGRESSION_PREREG_V2" or result["protocol_version"] != packets["protocol_version"]:
        raise ValueError("Phase 6 protocol V2 metadata mismatch.")
    if evaluator.get("evaluator_version") != "PRESENCE_REGRESSION_EVAL_V2":
        raise ValueError("Phase 6 evaluator V2 metadata mismatch.")
    history = result.get("protocol_history", {})
    if "REPLACED BEFORE ANY PHASE 6 HOST TRIAL" not in history.get("PHASE6_PRESENCE_REGRESSION_PREREG_V1", ""):
        raise ValueError("Phase 6 V1 replacement history is missing.")
    if history.get("PHASE6_PRESENCE_REGRESSION_PREREG_V2") != "FROZEN BEFORE FIRST PHASE 6 HOST TRIAL":
        raise ValueError("Phase 6 V2 freeze history is missing.")
    if result.get("instruction_scope") != "TASK_SCOPED_SYNTHETIC_CAPABILITY_INSTRUCTIONS":
        raise ValueError("Phase 6 task-scoped instruction boundary is missing.")
    if model_visible_packet_digest(rows) != result["model_visible_packet_digest"]:
        raise ValueError("Phase 6 model-visible packet digest mismatch.")
    frozen_text_hashes = result.get("frozen_packet_text_sha256_by_trial", {})
    if set(frozen_text_hashes) != set(packet_ids) or any(
        sha256(row["model_visible_text"].encode("utf-8")) != frozen_text_hashes[row["trial_id"]] for row in rows
    ):
        raise ValueError("Phase 6 model-visible packet text changed from V1.")
    screen = json.loads(SKILL_SCREEN_PATH.read_text(encoding="utf-8"))
    screen_summary = validate_skill_screen(screen)
    if sha256(SKILL_SCREEN_PATH.read_bytes()) != result["real_skill_screening"]["evidence_sha256"]:
        raise ValueError("Static Skill screen evidence hash mismatch.")

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
        "model_visible_packet_digest": model_visible_packet_digest(rows),
        "skill_screen_row_count": len(screen["rows"]),
        "skill_screen_selected_candidate_count": screen["selected_candidate_count"],
        "skill_screen_source_counts": screen_summary["source_counts"],
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
