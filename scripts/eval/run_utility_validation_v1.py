"""Offline-testable Codex CLI controller for Utility Validation v1.

This module defines an eval-only transport. It does not establish production
Host delivery, model-visible content, Skill use, or authorization to run trials.
No command is executed unless a future caller explicitly invokes the controller.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from scripts.eval.utility_validation_observation import (
    TrialSession,
    UtilityObservationLedger,
    FROZEN_SANITIZED_CODEX_ARGV,
    canonical_json,
    sha256_bytes,
    unavailable_host_usage,
)


CONTROLLER_VERSION = "UTILITY_VALIDATION_CODEX_CLI_CONTROLLER_V1"
INVOCATION_SPEC_ID = "UTILITY_VALIDATION_CODEX_EXEC_V1"
SANITIZED_ARGV = FROZEN_SANITIZED_CODEX_ARGV
OFFICIAL_CODEX_EXEC_ARGS = SANITIZED_ARGV[1:]
SANITIZED_ARGV_SHA256 = sha256_bytes(canonical_json(list(SANITIZED_ARGV)))
_KNOWN_ITEM_TOOL_TYPES = {
    "command_execution": "command_execution",
    "mcp_tool_call": "mcp_tool_call",
    "web_search_call": "web_search_call",
    "file_search_call": "file_search_call",
    "computer_call": "computer_call",
}
_NON_TOOL_ITEM_TYPES = {"agent_message", "reasoning", "plan_update", "trace", "image"}


@dataclass(frozen=True)
class ParsedHostOutput:
    thread_id: str | None
    thread_started: bool
    turn_completed: bool
    output_sha256: str | None
    output_bytes: int | None
    event_parse_status: str
    malformed_line_count: int
    unknown_event_count: int
    tool_activity: tuple[dict, ...]
    usage: dict


def _bytes(value) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8", errors="replace")
    return b""


def _recognized_tool_category(name: str) -> str | None:
    if name in _KNOWN_ITEM_TOOL_TYPES:
        return _KNOWN_ITEM_TOOL_TYPES[name]
    if name.startswith("mcp_") or name.startswith("mcp."):
        return "mcp_tool_call"
    return None


def parse_codex_jsonl(stdout: bytes | str | None) -> ParsedHostOutput:
    """Extract allow-listed event summaries; never return raw response/arguments."""
    raw = _bytes(stdout)
    malformed = 0
    unknown = 0
    thread_ids: list[str] = []
    turn_completed = False
    output_parts: list[bytes] = []
    tool_calls: dict[tuple[str, str], str] = {}
    usage = unavailable_host_usage()
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            malformed += 1
            continue
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            unknown += 1
            continue
        event_type = event["type"]
        if event_type == "thread.started":
            thread_id = event.get("thread_id")
            if isinstance(thread_id, str) and thread_id and len(thread_id) <= 128:
                thread_ids.append(thread_id)
            else:
                malformed += 1
        elif event_type in {"turn.started", "turn.failed", "turn.cancelled"}:
            continue
        elif event_type == "turn.completed":
            turn_completed = True
            raw_usage = event.get("usage")
            if raw_usage is not None:
                if not isinstance(raw_usage, dict):
                    malformed += 1
                else:
                    for name in usage:
                        value = raw_usage.get(name)
                        if value is None:
                            continue
                        if type(value) is not int or value < 0:
                            malformed += 1
                            continue
                        usage[name] = {"value": value, "provenance": "HOST_DECLARED"}
        elif event_type in {"item.started", "item.updated", "item.completed"}:
            item = event.get("item")
            if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                malformed += 1
                continue
            item_type = item["type"]
            if item_type == "agent_message":
                text = item.get("text")
                if event_type == "item.completed" and isinstance(text, str):
                    output_parts.append(text.encode("utf-8"))
            elif item_type in _NON_TOOL_ITEM_TYPES:
                continue
            else:
                category = _recognized_tool_category(item_type) or "unknown_tool_event"
                identifier = item.get("id")
                if not isinstance(identifier, str) or not identifier:
                    identifier = f"line:{line_number}"
                tool_calls[(category, identifier)] = category
                if category == "unknown_tool_event":
                    unknown += 1
        elif event_type.startswith("tool.") or event_type.startswith("mcp."):
            category = "mcp_tool_call" if event_type.startswith("mcp.") else "unknown_tool_event"
            identifier = event.get("call_id") or event.get("item_id")
            if not isinstance(identifier, str) or not identifier:
                identifier = f"line:{line_number}"
            tool_calls[(category, identifier)] = category
        elif event_type == "agent_message":
            text = event.get("text")
            if isinstance(text, str):
                output_parts.append(text.encode("utf-8"))
            else:
                malformed += 1
        else:
            # Preserve the fact that the current allow-list did not understand
            # this event; do not guess its semantics from adjacent text.
            unknown += 1
    # An invocation should produce exactly one thread.started identity. Even
    # repeated copies of the same event are ambiguous at the protocol boundary.
    thread_id = thread_ids[0] if len(thread_ids) == 1 else None
    output = b"\n".join(output_parts) if output_parts else None
    counts: dict[str, int] = {}
    for category in tool_calls.values():
        counts[category] = counts.get(category, 0) + 1
    tool_activity = tuple({"event_type": key, "count": counts[key]} for key in sorted(counts))
    complete = malformed == 0 and unknown == 0 and thread_id is not None and turn_completed and output is not None
    return ParsedHostOutput(
        thread_id=thread_id,
        thread_started=thread_id is not None,
        turn_completed=turn_completed,
        output_sha256=sha256_bytes(output) if output is not None else None,
        output_bytes=len(output) if output is not None else None,
        event_parse_status="COMPLETE" if complete else "INCOMPLETE",
        malformed_line_count=malformed,
        unknown_event_count=unknown,
        tool_activity=tool_activity,
        usage=usage,
    )


def _extract_agent_output_bytes(stdout: bytes | str | None) -> bytes | None:
    """Return the transient agent message for an evaluator; never persist it."""
    parts = []
    for line in _bytes(stdout).splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(event, dict):
            return None
        if event.get("type") == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                parts.append(item["text"].encode("utf-8"))
        elif event.get("type") == "agent_message" and isinstance(event.get("text"), str):
            parts.append(event["text"].encode("utf-8"))
    return b"\n".join(parts) if parts else None


def evaluate_blind(evaluator: Callable, *, output_bytes: bytes, rubric_ref: str,
                   rubric_sha256: str, retry_count: int) -> dict:
    """Call a shared evaluator without passing condition or Nexus metadata.

    The evaluator receives only transient output bytes and the same rubric
    identity for A/B. Its result is reduced to allow-listed acceptance facts.
    """
    if not isinstance(output_bytes, bytes) or type(retry_count) is not int or retry_count < 0:
        raise ValueError("BLIND_EVALUATOR_INPUT_INVALID")
    if len(rubric_sha256) != 64 or any(char not in "0123456789abcdef" for char in rubric_sha256):
        raise ValueError("BLIND_EVALUATOR_RUBRIC_HASH_INVALID")
    raw_result = evaluator(
        output_bytes=output_bytes,
        rubric_ref=rubric_ref,
        rubric_sha256=rubric_sha256,
    )
    required = {"verdict", "evidence_refs", "evidence_sha256", "inconclusive_reason"}
    if not isinstance(raw_result, dict) or set(raw_result) != required:
        raise ValueError("BLIND_EVALUATOR_RESULT_INVALID")
    verdict = raw_result["verdict"]
    if verdict not in {"PASS", "FAIL", "INCONCLUSIVE"}:
        raise ValueError("BLIND_EVALUATOR_VERDICT_INVALID")
    return {
        "evaluated_output_sha256": sha256_bytes(output_bytes),
        "verdict": verdict,
        "evidence_refs": raw_result["evidence_refs"],
        "evidence_sha256": raw_result["evidence_sha256"],
        "first_pass": verdict == "PASS" and retry_count == 0,
        "retry_count": retry_count,
        "inconclusive_reason": raw_result["inconclusive_reason"],
    }


def _external_empty_cwd(temp_root: str | Path | None, repository_root: Path):
    """Yield a fresh empty cwd and never persist its local path."""
    class _Cwd:
        def __init__(self):
            self._manager = tempfile.TemporaryDirectory(prefix="nexus-utility-v1-", dir=temp_root)
            self.path = Path(self._manager.name).resolve()

        def __enter__(self):
            try:
                self.path.relative_to(repository_root.resolve())
            except ValueError:
                pass
            else:
                self._manager.cleanup()
                raise RuntimeError("CONTROLLER_CWD_MUST_BE_REPO_EXTERNAL")
            if any(self.path.iterdir()):
                self._manager.cleanup()
                raise RuntimeError("CONTROLLER_CWD_MUST_BE_EMPTY")
            return self.path

        def __exit__(self, exc_type, exc, tb):
            self._manager.cleanup()

    return _Cwd()


class CodexCliController:
    """One fresh CLI invocation, with exact input/argv provenance and no retry."""

    def __init__(self, ledger: UtilityObservationLedger, *, repository_root: str | Path,
                 temp_root: str | Path | None = None,
                 subprocess_runner: Callable | None = None,
                 monotonic_ns: Callable[[], int] = time.monotonic_ns):
        self.ledger = ledger
        self.repository_root = Path(repository_root).resolve()
        self.temp_root = temp_root
        self.subprocess_runner = subprocess_runner
        self.monotonic_ns = monotonic_ns

    @staticmethod
    def new_invocation_id() -> str:
        return str(uuid.uuid4())

    def invoke(self, session: TrialSession, input_bytes: bytes, *,
               controller_invocation_id: str | None = None,
               cli_version: str | None = None, timeout_seconds: int = 180,
               acceptance_evaluator: Callable | None = None,
               evaluator_kind: str = "DETERMINISTIC") -> dict:
        if not isinstance(input_bytes, bytes):
            raise TypeError("CLI input must be exact UTF-8 bytes")
        if type(timeout_seconds) is not int or timeout_seconds < 1:
            raise ValueError("timeout_seconds must be a positive integer")
        invocation_id = controller_invocation_id or self.new_invocation_id()
        argv = list(SANITIZED_ARGV)
        input_sha = sha256_bytes(input_bytes)
        prior_events = self.ledger.trial_events(session.study_id, session.trial_id)
        attempt_number = 1 + sum(item["event_type"] == "HOST_INVOCATION_STARTED" for item in prior_events)
        self.ledger.append_event(
            study_id=session.study_id, pair_id=session.pair_id, trial_id=session.trial_id,
            event_type="HOST_INVOCATION_STARTED", payload={
                "controller_invocation_id": invocation_id,
                "attempt_number": attempt_number,
                "sanitized_argv": argv,
                "sanitized_argv_sha256": sha256_bytes(canonical_json(argv)),
                "cli_version": cli_version,
                "cli_version_provenance": "HOST_DECLARED" if cli_version is not None else "UNAVAILABLE",
                "input_sha256": input_sha, "input_bytes": len(input_bytes),
                "subprocess_shell": False,
                "cwd_policy": "FRESH_EMPTY_REPO_EXTERNAL", "host_surface": "CODEX_CLI",
            },
        )
        runner = self.subprocess_runner or subprocess.run
        stdout = b""
        stderr = b""
        exit_code = None
        elapsed_ms = None
        timed_out = False
        controller_failure = False
        input_submission_confirmed = False
        try:
            with _external_empty_cwd(self.temp_root, self.repository_root) as cwd:
                process_start_ns = self.monotonic_ns()
                try:
                    completed = runner(
                        argv, input=input_bytes, cwd=str(cwd), shell=False,
                        capture_output=True, timeout=timeout_seconds, check=False,
                    )
                except subprocess.TimeoutExpired as exc:
                    elapsed_ms = max(0, (self.monotonic_ns() - process_start_ns) // 1_000_000)
                    raise exc
                elapsed_ms = max(0, (self.monotonic_ns() - process_start_ns) // 1_000_000)
                # Returning from the direct subprocess API proves only that
                # these exact bytes were submitted to the CLI stdin boundary.
                # It does not prove model visibility or use.
                input_submission_confirmed = True
            stdout = _bytes(getattr(completed, "stdout", b""))
            stderr = _bytes(getattr(completed, "stderr", b""))
            exit_code = getattr(completed, "returncode", None)
            if type(exit_code) is not int:
                controller_failure = True
        except subprocess.TimeoutExpired as exc:
            stdout = _bytes(exc.stdout)
            stderr = _bytes(exc.stderr)
            timed_out = True
        except Exception:
            # Do not persist exception strings: they can contain private paths.
            controller_failure = True
        parsed = parse_codex_jsonl(stdout)
        prior_thread_ids = {
            event["payload"]["thread_id"]
            for event in self.ledger.events()
            if event["event_type"] == "HOST_INVOCATION_COMPLETED"
            and event["payload"].get("thread_id") is not None
        }
        thread_reused = parsed.thread_id in prior_thread_ids if parsed.thread_id else False
        self.ledger.append_event(
            study_id=session.study_id, pair_id=session.pair_id, trial_id=session.trial_id,
            event_type="HOST_INVOCATION_COMPLETED", payload={
                "controller_invocation_id": invocation_id,
                "attempt_number": attempt_number,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "thread_started": parsed.thread_started,
                "thread_id": parsed.thread_id,
                "turn_completed": parsed.turn_completed,
                "agent_output_sha256": parsed.output_sha256,
                "agent_output_bytes": parsed.output_bytes,
                "host_process_elapsed_ms": elapsed_ms,
                "event_parse_status": "INCOMPLETE" if controller_failure else parsed.event_parse_status,
                "malformed_line_count": parsed.malformed_line_count,
                "unknown_event_count": parsed.unknown_event_count,
                "thread_id_reused": thread_reused,
                "tool_activity": list(parsed.tool_activity),
                "usage": parsed.usage,
                "usage_basis": (
                    "HOST_REPORTED_TOTAL_TURN_TOKEN_TELEMETRY"
                    if any(item["value"] is not None for item in parsed.usage.values())
                    else "UNAVAILABLE"
                ),
                "stderr_bytes": len(stderr),
                "stderr_content_status": "UNAVAILABLE" if controller_failure else ("NONEMPTY" if stderr else "EMPTY"),
                "input_submission_status": (
                    "CONTROLLER_SUBMITTED_TO_CODEX_CLI"
                    if input_submission_confirmed else "SUBMISSION_UNCONFIRMED"
                ),
                "received_intervention": (
                    ("EVAL_INTERVENTION_TRANSPORT" if session.condition == "NEXUS_ACTIVE" else "NONE")
                    if input_submission_confirmed else "UNCONFIRMED"
                ),
                "submitted_input_sha256": input_sha if input_submission_confirmed else None,
                "submitted_input_bytes": len(input_bytes) if input_submission_confirmed else None,
            },
        )
        if controller_failure:
            session.inconclusive("CONTROLLER_FAILURE", host_process_elapsed_ms=elapsed_ms)
        elif timed_out:
            session.inconclusive("CONTROLLER_FAILURE", host_process_elapsed_ms=elapsed_ms)
        elif parsed.thread_id is None:
            session.inconclusive("HOST_INVOCATION_ID_MISSING", host_process_elapsed_ms=elapsed_ms)
        elif parsed.event_parse_status != "COMPLETE":
            session.inconclusive("HOST_EVENT_PARSE_INCOMPLETE", host_process_elapsed_ms=elapsed_ms)
        elif thread_reused:
            session.ledger.append_event(
                study_id=session.study_id, pair_id=session.pair_id, trial_id=session.trial_id,
                event_type="TRIAL_INVALIDATED", payload={
                    "reason_code": "CROSS_CONDITION_CONTAMINATION",
                    "trial_wall_elapsed_ms": session.elapsed_ms(),
                    "host_process_elapsed_ms": elapsed_ms, "evidence_refs": [],
                },
            )
        elif exit_code != 0:
            session.inconclusive("CONTROLLER_FAILURE", host_process_elapsed_ms=elapsed_ms)
        elif acceptance_evaluator is not None:
            try:
                opened = self.ledger.trial_events(session.study_id, session.trial_id)[0]["payload"]
                agent_output = _extract_agent_output_bytes(stdout)
                if agent_output is None:
                    raise ValueError("ACCEPTANCE_OUTPUT_UNAVAILABLE")
                accepted = evaluate_blind(
                    acceptance_evaluator, output_bytes=agent_output,
                    rubric_ref=opened["acceptance_rubric_ref"],
                    rubric_sha256=opened["acceptance_rubric_sha256"],
                    retry_count=attempt_number - 1,
                )
                accepted["evaluator_kind"] = evaluator_kind
                accepted["rubric_ref"] = opened["acceptance_rubric_ref"]
                accepted["rubric_sha256"] = opened["acceptance_rubric_sha256"]
                session.record_acceptance(**accepted)
                session.complete()
            except Exception:
                # Evaluator exceptions may include private material. Preserve
                # only an incomplete status; never infer a verdict from stderr.
                session.inconclusive("ACCEPTANCE_INCOMPLETE", host_process_elapsed_ms=elapsed_ms)
        # The return value contains only allow-listed summaries and hashes.
        return {
            "controller_invocation_id": invocation_id,
            "attempt_number": attempt_number,
            "invocation_spec_id": INVOCATION_SPEC_ID,
            "execution_harness_version": CONTROLLER_VERSION,
            "sanitized_argv": list(SANITIZED_ARGV),
            "sanitized_argv_sha256": SANITIZED_ARGV_SHA256,
            "input_sha256": input_sha, "input_bytes": len(input_bytes),
            "exit_code": exit_code, "timed_out": timed_out,
            "thread_started": parsed.thread_started, "thread_id": parsed.thread_id,
            "turn_completed": parsed.turn_completed,
            "agent_output_sha256": parsed.output_sha256,
            "agent_output_bytes": parsed.output_bytes,
            "tool_activity": list(parsed.tool_activity),
            "usage": parsed.usage,
            "usage_basis": (
                "HOST_REPORTED_TOTAL_TURN_TOKEN_TELEMETRY"
                if any(item["value"] is not None for item in parsed.usage.values())
                else "UNAVAILABLE"
            ),
            "event_parse_status": parsed.event_parse_status,
            "host_process_elapsed_ms": elapsed_ms,
            "stderr_bytes": len(stderr),
            "stderr_content_status": "UNAVAILABLE" if controller_failure else ("NONEMPTY" if stderr else "EMPTY"),
            "input_submission_status": (
                "CONTROLLER_SUBMITTED_TO_CODEX_CLI"
                if input_submission_confirmed else "SUBMISSION_UNCONFIRMED"
            ),
            "received_intervention": (
                ("EVAL_INTERVENTION_TRANSPORT" if session.condition == "NEXUS_ACTIVE" else "NONE")
                if input_submission_confirmed else "UNCONFIRMED"
            ),
            "retry_performed": False,
            "raw_output_persisted": False,
            "controller_failure": controller_failure,
            "trial_wall_elapsed_ms": (
                next((event["payload"]["trial_wall_elapsed_ms"] for event in reversed(self.ledger.trial_events(session.study_id, session.trial_id))
                      if event["event_type"] in {"TRIAL_COMPLETED", "TRIAL_INCONCLUSIVE"}), None)
            ),
        }
