"""Validate and execute the frozen Phase 6 matrix through the accepted Phase 5 Host adapter."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from scripts.eval.run_behavioral_phase5 import (
    ARGV_HASH_RULE,
    EXECUTION_HARNESS_VERSION as PHASE5_EXECUTION_HARNESS_VERSION,
    FROZEN_SANITIZED_ARGV,
    OFFICIAL_INVOCATION_SPEC_ID,
    PRIVATE_CAPTURE_LOCATION,
    _argv_sha256,
    _execute_host as _phase5_execute_host,
    _external_capture_dir,
    _extract_events,
    _host_command,
    _serialize_persisted_result,
)


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
NEGATIVE_CRITERIA = SCREEN_CRITERIA[:4]
POSITIVE_CRITERIA = SCREEN_CRITERIA[4:]
NEGATIVE_LABELS = {
    "requires_secret_or_auth": "SECRET_OR_AUTH",
    "requires_network": "NETWORK",
    "requires_filesystem_mutation": "FILESYSTEM_MUTATION",
    "requires_shell_or_system_mutation": "SHELL_OR_SYSTEM_MUTATION",
}
POSITIVE_FAILURES = {
    "deterministic_task_available": "NO_DETERMINISTIC_TASK_AVAILABLE",
    "narrow_trigger": "TRIGGER_NOT_NARROW",
    "instruction_boundary_clear": "INSTRUCTION_BOUNDARY_NOT_CLEAR",
    "safe_unrelated_control_constructible": "NO_SAFE_UNRELATED_CONTROL",
}
FORMAL_CONTROLLER = "EXTERNAL_WINDOWS_POWERSHELL"
AUTHORIZATION_BASIS = "EXTERNAL_REVIEW_ACCEPTED_PHASE6_PREREG_V2"
EXECUTION_HARNESS_VERSION = "PHASE6_EXTERNAL_EXECUTION_ADAPTER_V1"
CONTROLLER_ENV = "NEXUS_PHASE6_EXTERNAL_CONTROLLER"
USAGE_BASIS = "HOST_REPORTED_TOTAL_TURN_TOKEN_TELEMETRY"
PRIVATE_CAPTURE_PATH = PRIVATE_CAPTURE_LOCATION


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
    if set(criteria) != set(SCREEN_CRITERIA):
        raise ValueError("Static-screen criteria must contain exactly the eight frozen criteria.")
    for name in NEGATIVE_CRITERIA:
        value = criteria[name]
        if value == "TRUE":
            reasons.append(f"REQUIRES_{NEGATIVE_LABELS[name]}")
        elif value == "UNKNOWN":
            reasons.append(f"UNKNOWN_{NEGATIVE_LABELS[name]}")
        elif value != "FALSE":
            raise ValueError(f"Invalid static-screen criterion value for {name}: {value}")
    for name in POSITIVE_CRITERIA:
        value = criteria[name]
        if value == "FALSE":
            reasons.append(POSITIVE_FAILURES[name])
        elif value == "UNKNOWN":
            reasons.append(f"UNKNOWN_{name.upper()}")
        elif value != "TRUE":
            raise ValueError(f"Invalid static-screen criterion value for {name}: {value}")
    return reasons


def screen_candidate(criteria: dict[str, str]) -> dict[str, Any]:
    reasons = derive_screen_exclusion_reasons(criteria)
    eligible = all(criteria[name] == "FALSE" for name in NEGATIVE_CRITERIA) and all(
        criteria[name] == "TRUE" for name in POSITIVE_CRITERIA
    )
    return {"selection_eligible": eligible, "exclusion_reasons": reasons}


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
        derived = screen_candidate(criteria)
        reasons = derived["exclusion_reasons"]
        eligible = derived["selection_eligible"]
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


def _persist_result(path: Path, result: dict[str, Any]) -> None:
    result["raw_capture_location"] = PRIVATE_CAPTURE_PATH
    path.write_text(_serialize_persisted_result(result), encoding="utf-8")


def _classify_execution(executed: dict[str, Any], message: str | None, session_id: str | None,
                        turn_completed: bool, tool_calls: int | str,
                        seen_thread_ids: set[str]) -> tuple[str, bool]:
    if executed.get("timed_out") is True:
        return "TIMEOUT", False
    repeated_thread = bool(session_id and session_id in seen_thread_ids)
    if session_id:
        seen_thread_ids.add(session_id)
    if repeated_thread:
        return "FRESH_THREAD_ID_REUSED", False
    if tool_calls == "UNAVAILABLE" or (isinstance(tool_calls, int) and tool_calls > 0):
        return "CONTAMINATED_BY_TOOL_USE", False
    exit_code = executed.get("exit_code")
    if exit_code != 0 and not session_id:
        return "PRE_MODEL_HOST_INVOCATION_FAILURE", False
    eligible = exit_code == 0 and session_id is not None and turn_completed and bool(message and message.strip()) and tool_calls == 0
    if eligible:
        return "COMPLETED", True
    return "INCOMPLETE_HOST_EXECUTION", False


def run_formal_matrix(
    *,
    packets_path: Path = PACKETS_PATH,
    evaluator_path: Path = EVALUATOR_PATH,
    results_path: Path = RESULT_PATH,
    timeout: int = 180,
    capture_dir: Path | None = None,
) -> dict[str, Any]:
    """Execute the once-only authorized matrix; intended for external PowerShell only."""
    prior = json.loads(results_path.read_text(encoding="utf-8"))
    if os.environ.get(CONTROLLER_ENV) != FORMAL_CONTROLLER:
        raise SystemExit(f"Set {CONTROLLER_ENV}=EXTERNAL_WINDOWS_POWERSHELL in the current external PowerShell process.")
    if prior.get("formal_execution_controller") != FORMAL_CONTROLLER or prior.get("formal_execution_authorized") is not True:
        raise SystemExit("Refusing Host execution: external execution authorization metadata is absent.")
    if prior.get("authorization_basis") != AUTHORIZATION_BASIS:
        raise SystemExit("Refusing Host execution: authorization basis mismatch.")
    if prior.get("formal_execution_authorization_state", "AVAILABLE") != "AVAILABLE" or prior.get("currently_execution_authorized", True) is not True:
        raise SystemExit("Refusing repeat Phase 6 execution: historical authorization has been consumed.")
    if prior.get("execution_count", 0) != 0 or prior.get("attempted_trial_count", 0) != 0 or prior.get("formal_trials"):
        raise SystemExit("Refusing repeat Phase 6 execution; preserve the first execution record.")
    if prior.get("formal_trial_count", 0) != 0 or prior.get("nexus_model_receipt_count", 0) != 0:
        raise SystemExit("Refusing Host execution: prior trials or MODEL receipts are present.")

    validate_preregistration(packets_path, evaluator_path, results_path)
    packets = json.loads(packets_path.read_text(encoding="utf-8"))
    evaluator = json.loads(evaluator_path.read_text(encoding="utf-8"))
    packet_by_id = {row["trial_id"]: row for row in packets["packets"]}
    order = packets["execution_order"]
    if prior.get("execution_order_sha256") != packets["execution_order_sha256"]:
        raise SystemExit("Refusing Host execution: frozen order differs from authorized result metadata.")
    codex = shutil.which("codex")
    if not codex:
        raise SystemExit("Host execution unavailable: codex CLI not found on PATH.")
    _, provenance = _host_command(codex)
    if tuple(provenance["sanitized_argv"]) != FROZEN_SANITIZED_ARGV:
        raise SystemExit("Refusing Host execution: sanitized argv does not match Phase 5 frozen invocation.")
    if capture_dir is None:
        capture_dir = _external_capture_dir(Path(tempfile.mkdtemp(prefix="nexus-academy-phase6-captures-")))
    else:
        capture_dir = _external_capture_dir(capture_dir)
        capture_dir.mkdir(parents=True, exist_ok=True)

    result = dict(prior)
    result.update({
        "status": "HOST_TRIALS_IN_PROGRESS",
        "formal_execution_authorization_state": "CONSUMED",
        "currently_execution_authorized": False,
        "execution_count": 1,
        "attempted_trial_count": 0,
        "formal_trial_count": 0,
        "formal_trials": {},
        "raw_capture_location": PRIVATE_CAPTURE_PATH,
        "nexus_model_receipt_count": 0,
        "host_model_identity": "UNAVAILABLE",
        "provider_dollar_cost": "UNAVAILABLE",
        "per_exposure_token_attribution": "UNAVAILABLE",
        "ambient_host_input_composition": "UNAVAILABLE / NOT DECOMPOSABLE",
        "cross_session_memory_confound": "UNCHARACTERIZED",
        "sanitized_argv": provenance["sanitized_argv"],
        "sanitized_argv_sha256": provenance["sanitized_argv_sha256"],
        "argv_hash_rule": ARGV_HASH_RULE,
    })
    _persist_result(results_path, result)
    seen_thread_ids: set[str] = set()

    for index, trial_id in enumerate(order, start=1):
        packet_row = packet_by_id[trial_id]
        packet = packet_row["model_visible_text"]
        mapping = evaluator["trials"][trial_id]

        def persist_invocation(raw_provenance: dict[str, Any]) -> None:
            phase6_provenance = {
                **raw_provenance,
                "execution_harness_version": EXECUTION_HARNESS_VERSION,
                "invocation_spec_id": OFFICIAL_INVOCATION_SPEC_ID,
                "underlying_invocation_adapter_version": PHASE5_EXECUTION_HARNESS_VERSION,
                "subprocess_shell": False,
                "argv_persisted_before_process_outcome": True,
            }
            result["attempted_trial_count"] = index
            result["formal_trials"][trial_id] = {
                "trial_id": trial_id,
                "family_id": packet_row["family_id"],
                "presence": packet_row["presence"],
                "task_kind": packet_row["task_kind"],
                "packet_sha256": packet_row["packet_sha256"],
                "execution_status": "INVOCATION_IN_PROGRESS",
                "formal_trial_counted": False,
                **phase6_provenance,
            }
            _persist_result(results_path, result)

        executed = _phase5_execute_host(
            codex, packet, timeout, capture_dir, trial_id, persist_invocation,
        )
        stdout = executed.pop("_stdout", "")
        message, usage, tool_calls, session_id, turn_completed = _extract_events(stdout)
        status, eligible = _classify_execution(executed, message, session_id, turn_completed, tool_calls, seen_thread_ids)
        observed = {
            **result["formal_trials"][trial_id],
            "execution_status": status,
            "exit_code": executed.get("exit_code"),
            "timed_out": executed.get("timed_out"),
            "thread_started": session_id is not None,
            "thread_id": session_id,
            "turn_completed": turn_completed,
            "agent_output": message,
            "output_sha256": sha256(message.encode("utf-8")) if message is not None else None,
            "tool_call_count": tool_calls,
            "host_usage": usage if turn_completed else "UNAVAILABLE",
            "usage_basis": USAGE_BASIS,
            "stderr_classification": executed.get("stderr_classification"),
            "raw_capture_stdout": executed.get("raw_capture_stdout"),
            "raw_capture_stderr": executed.get("raw_capture_stderr"),
            "wall_clock_seconds": executed.get("wall_clock_seconds"),
            "formal_trial_counted": eligible,
            "relevant_utility": "NOT_APPLICABLE",
            "unrelated_task_preservation": "NOT_APPLICABLE",
        }
        if eligible and message is not None:
            scored = score_trial(message, mapping["expected"], mapping["task_kind"], mapping["presence"])
            observed.update(scored)
            result["formal_trial_count"] += 1
        result["formal_trials"][trial_id] = observed
        result["unique_thread_id_count"] = len(seen_thread_ids)
        result["nexus_model_receipt_count"] = 0
        _persist_result(results_path, result)
        if not eligible:
            result["status"] = "STOPPED_ON_PROTOCOL_CONDITION"
            result["blocking_trial_id"] = trial_id
            result["blocking_condition"] = status
            _persist_result(results_path, result)
            return result

    result["status"] = "FORMAL_MATRIX_COMPLETE"
    result["completed_trial_count"] = len(order)
    result["nexus_model_receipt_count"] = 0
    _persist_result(results_path, result)
    return result


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
    counters = ("execution_count", "attempted_trial_count", "formal_trial_count", "nexus_model_receipt_count")
    if any(type(result.get(key, 0)) is not int or result.get(key, 0) < 0 for key in counters):
        raise ValueError("Phase 6 execution counters must be nonnegative integers.")
    if result["nexus_model_receipt_count"] != 0:
        raise ValueError("External Academy CLI execution must not fabricate Nexus MODEL receipts.")
    formal_trials = result.get("formal_trials", {})
    if not isinstance(formal_trials, dict) or result["formal_trial_count"] > result["attempted_trial_count"]:
        raise ValueError("Phase 6 execution record counters or formal trial map are inconsistent.")
    if result.get("status") == "IN PROGRESS / FORMAL MATRIX AUTHORIZED — EXTERNAL EXECUTION PENDING":
        if result["execution_count"] != 0 or result["attempted_trial_count"] != 0 or result["formal_trial_count"] != 0 or formal_trials:
            raise ValueError("Pre-execution authorization state must have zero attempts and formal trials.")
        if result.get("formal_execution_authorization_state", "AVAILABLE") != "AVAILABLE" or result.get("currently_execution_authorized", True) is not True:
            raise ValueError("Pre-execution authorization must remain available until the first execution attempt.")
    elif result.get("status") == "STOPPED_ON_PROTOCOL_CONDITION":
        if result["execution_count"] != 1 or len(formal_trials) != result["attempted_trial_count"]:
            raise ValueError("Stopped execution record must preserve its one-shot attempt count and trial records.")
        counted = sum(row.get("formal_trial_counted") is True for row in formal_trials.values())
        if counted != result["formal_trial_count"]:
            raise ValueError("Stopped execution formal count does not match preserved trial records.")
        if result.get("formal_execution_authorization_state") != "CONSUMED" or result.get("currently_execution_authorized") is not False:
            raise ValueError("Stopped execution must preserve historical authorization as consumed and current authorization as false.")
        if result.get("retry_allowed") is not False or result.get("remaining_trials_executed") is not False:
            raise ValueError("Stopped execution must preserve its no-retry and remaining-trials boundary.")
        if result.get("phase_status") != "CLOSED / INCONCLUSIVE — FORMAL MATRIX EXHAUSTED":
            raise ValueError("Stopped Phase 6 experiment must retain the externally reviewed phase closure.")
        if result.get("external_review_status") != "CLOSED / INCONCLUSIVE" or result.get("external_review_decision") != "PHASE6_FORMAL_MATRIX_EXHAUSTED / INCONCLUSIVE":
            raise ValueError("Stopped Phase 6 experiment must retain the external review decision.")
    if result.get("execution_authorized") is not True or packets.get("execution_authorized") is not True:
        raise ValueError("Phase 6 external execution authorization metadata is missing.")
    if result.get("formal_execution_authorized") is not True or result.get("formal_execution_controller") != FORMAL_CONTROLLER:
        raise ValueError("Phase 6 formal controller authorization metadata is inconsistent.")
    if result.get("authorization_basis") != AUTHORIZATION_BASIS:
        raise ValueError("Phase 6 authorization basis is inconsistent.")
    _command, argv_provenance = _host_command("codex")
    if result.get("sanitized_argv") != argv_provenance["sanitized_argv"]:
        raise ValueError("Phase 6 sanitized argv differs from the canonical Phase 5 invocation.")
    if result.get("sanitized_argv_sha256") != _argv_sha256(FROZEN_SANITIZED_ARGV):
        raise ValueError("Phase 6 sanitized argv SHA-256 differs from the canonical Phase 5 invocation.")
    execution_was_authorized = result.get("formal_execution_authorized") is True
    authorization_state = result.get("formal_execution_authorization_state")
    if authorization_state is None:
        authorization_state = "CONSUMED" if result["execution_count"] or result["attempted_trial_count"] else "AVAILABLE"
    currently_authorized = authorization_state == "AVAILABLE" and result.get("currently_execution_authorized", True) is True
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
        "execution_count": result["execution_count"],
        "attempted_trial_count": result["attempted_trial_count"],
        "formal_trial_count": result["formal_trial_count"],
        "nexus_model_receipt_count": result["nexus_model_receipt_count"],
        "host_execution_was_authorized": execution_was_authorized,
        "host_execution_authorization_state": authorization_state,
        "host_execution_authorized": currently_authorized,
        "formal_execution_controller": FORMAL_CONTROLLER,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the frozen Phase 6 matrix or run its externally controlled once-only execution.")
    parser.add_argument("--validate", action="store_true", help="Validate frozen packets, evaluator, order, and result metadata.")
    parser.add_argument("--execute-host", action="store_true", help="Execute the once-only Phase 6 matrix from external Windows PowerShell only.")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    if args.execute_host:
        result = run_formal_matrix(timeout=args.timeout)
        print(json.dumps({
            "status": result["status"],
            "attempted_trial_count": result.get("attempted_trial_count", 0),
            "formal_trial_count": result.get("formal_trial_count", 0),
            "nexus_model_receipt_count": result.get("nexus_model_receipt_count", 0),
        }, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] == "FORMAL_MATRIX_COMPLETE" else 2
    print(json.dumps(validate_preregistration(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
