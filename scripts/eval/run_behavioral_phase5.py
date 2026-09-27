"""Run frozen blind Academy packets in separate ephemeral Codex CLI sessions.

This is an explicit external Host invocation harness, not a Nexus runtime or
Hosted Bridge. It never creates a MODEL Run/receipt and never reads credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PACKETS = ROOT / "eval" / "academy" / "fixtures" / "behavioral-phase5-packets.json"
DEFAULT_EVALUATOR = ROOT / "eval" / "academy" / "fixtures" / "behavioral-phase5-evaluator.json"
DEFAULT_RESULTS = ROOT / "eval" / "academy" / "results" / "behavioral-phase5.json"
OFFICIAL_INVOCATION_SPEC_ID = "PHASE5_OFFICIAL_HOST_INVOCATION_V1"
EXECUTION_HARNESS_VERSION = "PHASE5_EXECUTION_HARNESS_V3"
OFFICIAL_CODEX_EXEC_ARGS = (
    "exec",
    "--ephemeral",
    "--json",
    "--color",
    "never",
    "--sandbox",
    "read-only",
    "--skip-git-repo-check",
    "-",
)
FROZEN_SANITIZED_ARGV = ("codex", *OFFICIAL_CODEX_EXEC_ARGS)
ARGV_HASH_RULE = "SHA-256 of canonical UTF-8 JSON array (ensure_ascii=false; separators=(',', ':'))."
DIAGNOSTIC_SENTINEL = "Return exactly: P5_V3_HOST_OK"
CODEX_EXECUTABLE_OBSERVATION = "Resolved from PATH; absolute local path intentionally omitted."
PRIVATE_CAPTURE_LOCATION = "PRIVATE_EXTERNAL_CAPTURE_NOT_COMMITTED"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _argv_sha256(argv: tuple[str, ...] | list[str]) -> str:
    canonical = json.dumps(list(argv), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _sha256(canonical)


def _host_command(codex: str) -> tuple[list[str], dict[str, Any]]:
    command = [codex, *OFFICIAL_CODEX_EXEC_ARGS]
    if "--ask-for-approval" in command:
        raise RuntimeError("Forbidden Host argument detected before subprocess invocation.")
    sanitized_argv = ["codex", *command[1:]]
    if tuple(sanitized_argv) != FROZEN_SANITIZED_ARGV:
        raise RuntimeError("Constructed Host argv does not match the frozen official argv.")
    provenance = {
        "invocation_spec_id": OFFICIAL_INVOCATION_SPEC_ID,
        "execution_harness_version": EXECUTION_HARNESS_VERSION,
        "sanitized_argv": sanitized_argv,
        "sanitized_argv_sha256": _argv_sha256(sanitized_argv),
        "sanitized_argv_hash_rule": ARGV_HASH_RULE,
        "subprocess_shell": False,
        "cwd_observation": "FRESH_EMPTY_TEMPORARY_DIRECTORY_OUTSIDE_REPOSITORY",
        "argv_persisted_before_process_outcome": True,
    }
    return command, provenance


def _stderr_classification(stderr: str) -> str:
    lowered = stderr.lower()
    if not stderr.strip():
        return "EMPTY"
    if "unexpected argument" in lowered and "--ask-for-approval" in lowered:
        return "HOST_OR_WRAPPER_ARGUMENT_INJECTION_SUSPECTED"
    if "state db" in lowered or "app-server" in lowered or "access is denied" in lowered:
        return "HOST_STATE_INITIALIZATION_OR_ACCESS_FAILURE"
    return "NONEMPTY_UNCLASSIFIED"


def _load_json(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8")), raw


def _external_capture_dir(path: Path) -> Path:
    resolved = path.resolve()
    if resolved == ROOT.resolve() or ROOT.resolve() in resolved.parents:
        raise RuntimeError("Refusing to store raw Host output inside the Academy repository.")
    return resolved


def _serialize_persisted_result(result: dict[str, Any]) -> str:
    persisted = dict(result)
    persisted.pop("codex_executable", None)
    persisted["codex_executable_observation"] = CODEX_EXECUTABLE_OBSERVATION
    persisted["raw_capture_location"] = PRIVATE_CAPTURE_LOCATION
    return json.dumps(persisted, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _extract_events(stdout: str) -> tuple[str | None, dict[str, int] | None, int, str | None, bool]:
    messages: list[str] = []
    usage: dict[str, int] | None = None
    tool_ids: set[str] = set()
    session_id: str | None = None
    turn_completed = False
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started":
            session_id = event.get("thread_id")
        if event.get("type", "").startswith("item."):
            item = event.get("item") or {}
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                if event.get("type") == "item.completed":
                    messages.append(item["text"])
            elif item.get("type") not in {"reasoning", "plan_update"}:
                tool_ids.add(str(item.get("id") or f"{event.get('type')}:{item.get('type')}:{len(tool_ids)}"))
        elif event.get("type", "").startswith(("tool.", "mcp.")):
            tool_ids.add(str(event.get("call_id") or event.get("id") or event.get("type")))
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
            turn_completed = True
            usage = {key: value for key, value in event["usage"].items() if isinstance(value, int)}
    return (messages[-1] if messages else None), usage, len(tool_ids), session_id, turn_completed


def score(raw_output: str | None, expected: dict[str, str], distractors: list[str], *, conflict_trial: bool = False) -> dict[str, Any]:
    parsed: Any = None
    try:
        parsed = json.loads(raw_output) if raw_output is not None else None
    except (json.JSONDecodeError, TypeError):
        pass
    valid_shape = isinstance(parsed, dict) and set(parsed) == set(expected) and all(isinstance(value, str) for value in parsed.values())
    correct_pairs = sum(1 for key, value in expected.items() if isinstance(parsed, dict) and parsed.get(key) == value)
    unsupported = 0
    if isinstance(parsed, dict):
        unsupported += len(set(parsed) - set(expected))
        for key in set(parsed) & set(expected):
            if parsed[key] != expected[key] and parsed[key] != "UNKNOWN":
                unsupported += 1
    distractor_adoptions = sum(
        1 for value in (parsed.values() if isinstance(parsed, dict) else []) if value in distractors
    )
    return {
        "format_valid": bool(valid_shape),
        "exact_correct": bool(valid_shape and parsed == expected),
        "required_fact_coverage": correct_pairs / max(1, len(expected)),
        "unsupported_assertion_count": unsupported,
        "distractor_adoption_count": distractor_adoptions,
        "conflict_handling_correct": bool(valid_shape and parsed == expected) if conflict_trial else "NOT_APPLICABLE",
    }


def _execute_host(
    codex: str,
    stdin_text: str,
    timeout: int,
    capture_dir: Path,
    capture_stem: str,
    on_invocation: Any,
) -> dict[str, Any]:
    command, provenance = _host_command(codex)
    # Persist the exact sanitized argv before creating/running the subprocess so
    # failures and timeouts retain the invocation provenance.
    on_invocation(provenance)
    env = os.environ.copy()
    home = str(Path.home())
    env.setdefault("HOME", home)
    env.setdefault("CODEX_HOME", str(Path(home) / ".codex"))
    start = time.monotonic()
    stdout = ""
    stderr = ""
    exit_code: int | str = "TIMEOUT"
    timed_out = False
    try:
        with tempfile.TemporaryDirectory(prefix="nexus-academy-p5-") as workdir:
            cwd = Path(workdir).resolve()
            if cwd == ROOT.resolve() or ROOT.resolve() in cwd.parents or any(cwd.iterdir()):
                raise RuntimeError("Official Host cwd must be a new empty directory outside the Academy repository.")
            completed = subprocess.run(
                command, input=stdin_text, text=True, encoding="utf-8", errors="strict",
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env,
                timeout=timeout, check=False, shell=False,
            )
            stdout, stderr, exit_code = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = (exc.stdout or b"").decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = (exc.stderr or b"").decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    except (OSError, RuntimeError, UnicodeError) as exc:
        stderr = str(exc)

    stdout_path = capture_dir / f"{capture_stem}.stdout.jsonl"
    stderr_path = capture_dir / f"{capture_stem}.stderr.txt"
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    message, usage, tool_calls, session_id, turn_completed = _extract_events(stdout)
    return {
        **provenance,
        "thread_id": session_id,
        "turn_completed": turn_completed,
        "agent_output": message,
        "host_usage": usage,
        "tool_call_count": tool_calls if not timed_out else "UNAVAILABLE",
        "exit_code": exit_code,
        "timed_out": timed_out,
        "stderr_classification": _stderr_classification(stderr),
        "raw_capture_stdout": stdout_path.name,
        "raw_capture_stderr": stderr_path.name,
        "wall_clock_seconds": round(time.monotonic() - start, 3),
        "_stdout": stdout,
    }


def _run_one(
    codex: str,
    trial_id: str,
    packet: str,
    timeout: int,
    capture_dir: Path,
    seen_thread_ids: set[str],
    on_invocation: Any,
) -> dict[str, Any]:
    executed = _execute_host(codex, packet, timeout, capture_dir, trial_id, on_invocation)
    stdout = executed.pop("_stdout")
    message, usage, tool_calls, session_id, turn_completed = _extract_events(stdout)
    observed = not executed["timed_out"] and executed["exit_code"] == 0 and session_id is not None and turn_completed and message is not None
    repeated_thread = bool(session_id and session_id in seen_thread_ids)
    if session_id:
        seen_thread_ids.add(session_id)
    if repeated_thread:
        status = "FRESH_THREAD_ID_REUSED"
    elif tool_calls:
        status = "CONTAMINATED_BY_TOOL_USE"
    elif observed:
        status = "COMPLETED"
    elif session_id is None:
        status = "PRE_MODEL_HOST_INVOCATION_FAILURE"
    else:
        status = "INCOMPLETE_HOST_EXECUTION"
    return {
        **executed,
        "trial_id": trial_id,
        "execution_status": status,
        "host_execution_observed": observed,
        "formal_behavioral_trial_eligible": observed and not tool_calls and not repeated_thread,
        "host_session_id": session_id,
        "thread_started": session_id is not None,
        "turn_completed": turn_completed,
        "provider": "UNAVAILABLE",
        "host_model_identity": "UNAVAILABLE",
        "provider_request_id": "UNAVAILABLE",
        "output": message if observed else None,
        "output_sha256": _sha256(message.encode("utf-8")) if observed else None,
        "tool_call_count": tool_calls if not executed["timed_out"] else "UNAVAILABLE",
        "retry_count": "UNAVAILABLE",
        "host_usage": usage if turn_completed else "UNAVAILABLE",
        "explicit_packet_visibility": "KNOWN",
        "full_model_visible_context": "UNAVAILABLE",
        "ambient_host_context": "HELD_CONSTANT_BUT_PARTIALLY_OBSERVABLE",
    }


def run_diagnostic(*, results_path: Path, timeout: int) -> dict[str, Any]:
    result, _ = _load_json(results_path)
    diagnostics = result.setdefault("host_diagnostics", [])
    if any(item.get("execution_harness_version") == EXECUTION_HARNESS_VERSION for item in diagnostics):
        raise SystemExit("Refusing to repeat the V3 Host liveness diagnostic.")
    if result.get("formal_trial_count", 0) != 0 or result.get("nexus_model_receipt_count", 0) != 0:
        raise SystemExit("Refusing diagnostic: behavioral trial or MODEL receipt count is nonzero.")
    codex = shutil.which("codex")
    if not codex:
        raise SystemExit("Host liveness diagnostic unavailable: codex CLI not found on PATH.")
    capture_dir = _external_capture_dir(Path(tempfile.mkdtemp(prefix="nexus-academy-phase5-captures-")))
    diagnostic: dict[str, Any] = {
        "kind": "NON_BEHAVIORAL_HOST_LIVENESS_DIAGNOSTIC",
        "sentinel": DIAGNOSTIC_SENTINEL,
        "behavioral_trial_counted": False,
        "nexus_model_receipt_created": False,
    }

    def persist_invocation(provenance: dict[str, Any]) -> None:
        diagnostic.update(provenance)
        diagnostics.append(diagnostic)
        result["protocol_version"] = "PHASE5_PREREGISTRATION_V3"
        result["execution_harness_version"] = EXECUTION_HARNESS_VERSION
        result["status"] = "IN PROGRESS / V3 HOST DIAGNOSTIC RECORDED"
        results_path.write_text(_serialize_persisted_result(result), encoding="utf-8")

    executed = _execute_host(codex, DIAGNOSTIC_SENTINEL, timeout, capture_dir, "V3-liveness", persist_invocation)
    executed.pop("_stdout", None)
    diagnostic.update(executed)
    diagnostic["thread_started"] = diagnostic.get("thread_id") is not None
    if diagnostic.get("turn_completed") is not True:
        diagnostic["host_usage"] = "UNAVAILABLE"
        diagnostic["usage_basis"] = "UNAVAILABLE"
    else:
        diagnostic["usage_basis"] = "HOST_REPORTED_TOTAL_TURN_TOKEN_TELEMETRY"
    if diagnostic.get("thread_id") is None:
        diagnostic["tool_call_count"] = "UNAVAILABLE"
    message = diagnostic.get("agent_output")
    diagnostic["host_execution_observed"] = (
        diagnostic.get("exit_code") == 0
        and diagnostic.get("thread_id") is not None
        and diagnostic.get("turn_completed") is True
        and isinstance(message, str)
    )
    diagnostic["v3_host_execution_path"] = (
        "SUPPORTED" if diagnostic["host_execution_observed"] and diagnostic.get("tool_call_count") == 0
        else "BLOCKED"
    )
    diagnostic["thread_id"] = executed.get("thread_id")
    if diagnostic["stderr_classification"] == "HOST_OR_WRAPPER_ARGUMENT_INJECTION_SUSPECTED":
        result["v3_host_execution_path"] = "HOST_OR_WRAPPER_ARGUMENT_INJECTION_SUSPECTED"
        result["formal_matrix_status"] = "FORMAL_MATRIX_BLOCKED"
    else:
        result["v3_host_execution_path"] = diagnostic["v3_host_execution_path"]
    result["host_diagnostics"][-1] = diagnostic
    result["formal_trial_count"] = 0
    result["nexus_model_receipt_count"] = 0
    results_path.write_text(_serialize_persisted_result(result), encoding="utf-8")
    return result


def run(*, packets_path: Path, evaluator_path: Path, results_path: Path, timeout: int) -> dict[str, Any]:
    packet_doc, packet_bytes = _load_json(packets_path)
    evaluator, _ = _load_json(evaluator_path)
    prior, _ = _load_json(results_path)
    packet_hash = _sha256(packet_bytes)
    if prior.get("protocol_version") != "PHASE5_PREREGISTRATION_V3" or prior.get("fixture_sha256") != packet_hash:
        raise SystemExit("Refusing to run: results are not V3 for this exact frozen fixture.")
    if prior.get("formal_execution_authorized") is not True:
        raise SystemExit("Refusing to run formal trials: external review has not authorized execution.")
    if prior.get("execution_count", 0) != 0:
        raise SystemExit("Refusing to repeat Host trials; preserve the first execution record.")
    if packet_doc.get("official_invocation_spec_id") != OFFICIAL_INVOCATION_SPEC_ID:
        raise SystemExit("Refusing to run: official Host invocation spec is not frozen.")
    order = packet_doc.get("execution_order")
    if not isinstance(order, list) or packet_doc.get("execution_order_sha256") != _sha256(("\n".join(order) + "\n").encode("utf-8")):
        raise SystemExit("Refusing to run: preregistered execution order hash does not match.")
    codex = shutil.which("codex")
    if not codex:
        raise SystemExit("Automated Host invocation unavailable: codex CLI not found on PATH.")
    capture_dir = _external_capture_dir(Path(tempfile.mkdtemp(prefix="nexus-academy-phase5-captures-")))

    result = {
        **prior,
        "status": "HOST_TRIALS_IN_PROGRESS",
        "official_invocation_spec_id": OFFICIAL_INVOCATION_SPEC_ID,
        "official_command": "codex exec --ephemeral --json --color never --sandbox read-only --skip-git-repo-check -",
        "codex_executable_observation": CODEX_EXECUTABLE_OBSERVATION,
        "fixture_sha256": packet_hash,
        "evaluator_sha256": _sha256(evaluator_path.read_bytes()),
        "execution_count": 0,
        "formal_trial_count": 0,
        "execution_order": order,
        "raw_capture_location": PRIVATE_CAPTURE_LOCATION,
        "context_trials": {},
        "presence_trials": {},
        "host_baseline": {
            "primary_memory": "OFF",
            "tool_chat_memory": "ON_FORCED / USER_NOT_CONTROLLABLE",
            "cross_session_effect": "NOT INDEPENDENTLY VERIFIED",
            "custom_instructions": "ON / UNCHANGED",
            "global_agents": "UNCHANGED",
            "project_experience_curator": "UNCHANGED / DISCOVERED / UNEVALUATED",
        },
        "nexus_model_receipt_count": 0,
        "nexus_model_receipt_note": "Trials are external Academy CLI invocations, not CodexHostedBridge MODEL Runs; no synthetic Core receipt was created.",
    }

    packet_by_id = {trial["trial_id"]: trial["packet_text"] for trial in packet_doc["context_packets"]}
    context_mapping = evaluator["context_trials"]
    presence_mapping = evaluator["presence_trials"]
    if set(order) != set(context_mapping) | set(presence_mapping) or len(order) != len(set(order)):
        raise SystemExit("Refusing to run: packet order and evaluator mapping IDs differ.")
    seen_thread_ids: set[str] = set()
    result["status"] = "HOST_TRIALS_COMPLETED"
    for trial_id in order:
        packet = packet_by_id[trial_id]
        is_context = trial_id in context_mapping
        mapping = context_mapping[trial_id] if is_context else presence_mapping[trial_id]
        destination = result["context_trials"] if is_context else result["presence_trials"]

        def persist_invocation(provenance: dict[str, Any]) -> None:
            destination[trial_id] = {
                "trial_id": trial_id,
                "execution_status": "INVOCATION_IN_PROGRESS",
                "formal_trial_counted": False,
                **provenance,
            }
            results_path.write_text(_serialize_persisted_result(result), encoding="utf-8")

        observed = _run_one(codex, trial_id, packet, timeout, capture_dir, seen_thread_ids, persist_invocation)
        observed.update({
            "condition": mapping.get("condition") if is_context else None,
            "family": mapping.get("family") if is_context else None,
            "presence": mapping.get("presence") if not is_context else None,
            "task_type": mapping.get("task_type") if not is_context else None,
            "task_prompt_sha256": _sha256(packet.split("\n\nRecords:", 1)[0].encode("utf-8")) if is_context else _sha256(packet.split("\n\nCapability", 1)[0].encode("utf-8")),
            "full_exposed_packet_sha256": _sha256(packet.encode("utf-8")),
            "packet_bytes": len(packet.encode("utf-8")),
            "packet_chars": len(packet),
            "exposed_object_refs": [],
            "exposed_capability_refs": [] if is_context or mapping.get("presence") == "P0_ABSENT" else ["academy-capability:glyph-shift"],
        })
        observed["formal_trial_counted"] = observed["formal_behavioral_trial_eligible"]
        if observed["formal_behavioral_trial_eligible"]:
            observed.update(score(observed["output"], mapping["expected"], mapping["distractor_values"], conflict_trial=is_context and mapping.get("family") == "conflicting_sources"))
            result["execution_count"] += 1
            result["formal_trial_count"] += 1
        destination = result["context_trials"] if is_context else result["presence_trials"]
        destination[trial_id] = observed
        results_path.write_text(_serialize_persisted_result(result), encoding="utf-8")
        if not observed["formal_behavioral_trial_eligible"]:
            result["status"] = observed["execution_status"]
            result["blocking_trial_id"] = trial_id
            results_path.write_text(_serialize_persisted_result(result), encoding="utf-8")
            return result
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-host", action="store_true", help="Explicitly invoke the installed Codex CLI for each frozen trial")
    parser.add_argument("--diagnostic-host-liveness", action="store_true", help="Run one non-behavioral Host liveness sentinel using the frozen argv")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--packets", type=Path, default=DEFAULT_PACKETS)
    parser.add_argument("--evaluator", type=Path, default=DEFAULT_EVALUATOR)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args()
    if args.diagnostic_host_liveness:
        result = run_diagnostic(results_path=args.results, timeout=args.timeout)
        diagnostic = result["host_diagnostics"][-1]
        print(json.dumps({
            "status": diagnostic["v3_host_execution_path"],
            "exit_code": diagnostic["exit_code"],
            "thread_started": diagnostic["thread_id"] is not None,
            "turn_completed": diagnostic["turn_completed"],
            "tool_call_count": diagnostic["tool_call_count"],
            "nexus_model_receipt_count": result["nexus_model_receipt_count"],
        }, sort_keys=True))
        return 0 if diagnostic["v3_host_execution_path"] == "SUPPORTED" else 2
    if not args.execute_host:
        print("No Host calls made. Pass --execute-host only after reviewing the frozen packets and evaluator separation.")
        return 0
    result = run(packets_path=args.packets, evaluator_path=args.evaluator, results_path=args.results, timeout=args.timeout)
    print(json.dumps({"status": result["status"], "execution_count": result["execution_count"],
        "context_trial_count": len(result["context_trials"]), "presence_trial_count": len(result["presence_trials"]),
        "nexus_model_receipt_count": result["nexus_model_receipt_count"]}, sort_keys=True))
    return 0 if result["status"] == "HOST_TRIALS_COMPLETED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
