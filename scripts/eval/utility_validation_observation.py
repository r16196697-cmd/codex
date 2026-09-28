"""Repo-external, append-only observation ledger for Utility Validation v1.

This is evaluation evidence only. It is deliberately not a Nexus Task, Run,
Trace, Evidence, Memory, Object, or execution receipt.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator


SCHEMA_ID = "nexus.utility_observation_event"
SCHEMA_VERSION = 1
FROZEN_SANITIZED_CODEX_ARGV = (
    "codex", "exec", "--ephemeral", "--json", "--color", "never",
    "--sandbox", "read-only", "--skip-git-repo-check", "-",
)
CONDITIONS = frozenset({"HOST_NATIVE_BYPASS", "NEXUS_ACTIVE"})
EVENT_TYPES = frozenset({
    "TRIAL_OPENED", "INTERVENTION_PREPARED", "HOST_INVOCATION_STARTED",
    "HOST_INVOCATION_COMPLETED", "ACCEPTANCE_RECORDED", "TRIAL_COMPLETED",
    "TRIAL_INCONCLUSIVE", "TRIAL_EXCLUDED", "TRIAL_INVALIDATED",
})
REASON_CODES = frozenset({
    "INPUT_MISMATCH", "START_STATE_MISMATCH", "HOST_INVOCATION_ID_MISSING",
    "HOST_EVENT_PARSE_INCOMPLETE", "B_INTERVENTION_NOT_SUBMITTED",
    "CROSS_CONDITION_CONTAMINATION", "UNAUTHORIZED_SOURCE",
    "UNAUTHORIZED_TOOL_OR_NETWORK", "ACCEPTANCE_INCOMPLETE",
    "CONTROLLER_FAILURE", "PROVENANCE_AMBIGUOUS",
})
PROVENANCE = frozenset({"OBSERVED", "HOST_DECLARED", "DERIVED", "UNAVAILABLE"})
INTERVENTION_PREPARATION_STATUSES = frozenset({"PREPARED_NOT_SUBMITTED", "NONE_INCOMPLETE"})
INPUT_SUBMISSION_STATUSES = frozenset({"CONTROLLER_SUBMITTED_TO_CODEX_CLI", "SUBMISSION_UNCONFIRMED"})
SKILL_RESOLUTION_STATUSES = frozenset({"RESOLVED", "NO_MATCH", "AMBIGUOUS", "UNSUPPORTED", "INACTIVE_MODE"})
SKILL_RESOLUTIONS = frozenset({"HOST_NATIVE", "NEXUS_FALLBACK", "UNSUPPORTED"})
HOST_SKILL_AVAILABILITY = frozenset({"AVAILABLE", "UNAVAILABLE", "UNKNOWN"})
HOST_INVENTORY_PROVENANCE = frozenset({"ADAPTER_DISCOVERY", "HOST_DECLARED"})

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$")
_ENUM_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,127}$")
_ARGV_TOKEN_RE = re.compile(r"^[A-Za-z0-9-][A-Za-z0-9._:/-]{0,127}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_EVENT_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "eval" / "utility-validation-v1-observation.schema.json"


class ObservationLedgerError(ValueError):
    """A malformed, conflicting, or illegal observation-ledger operation."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ObservationLedgerError("LEDGER_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _safe_id(value: Any, field: str, *, ref: bool = False, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    pattern = _REF_RE if ref else _ID_RE
    if not isinstance(value, str) or not pattern.fullmatch(value) or ".." in value or "\\" in value:
        raise ObservationLedgerError(f"LEDGER_{field.upper()}_INVALID")


def _provenance_map(value: Any) -> None:
    if not isinstance(value, dict) or not value or any(
        not isinstance(key, str) or not _ID_RE.fullmatch(key) or status not in PROVENANCE
        for key, status in value.items()
    ):
        raise ObservationLedgerError("LEDGER_PROVENANCE_INVALID")


def _assert_no_private_path_or_body(value: Any, key: str = "") -> None:
    forbidden_keys = {
        "prompt", "raw_prompt", "response", "raw_response", "transcript", "conversation",
        "instruction_body", "context_body", "tool_arguments", "raw_tool_arguments",
        "absolute_path", "capture_path", "auth_state", "credential", "token", "cookie",
        "stdout", "stderr", "raw_jsonl", "private_config",
    }
    if key.lower() in forbidden_keys:
        raise ObservationLedgerError("LEDGER_PRIVATE_CONTENT_FIELD_FORBIDDEN")
    if isinstance(value, dict):
        for nested_key, nested in value.items():
            _assert_no_private_path_or_body(nested, str(nested_key))
    elif isinstance(value, list):
        for nested in value:
            _assert_no_private_path_or_body(nested, key)
    elif isinstance(value, str):
        if re.search(r"(?i)(?:^|[\s'\"])(?:[a-z]:\\|\\\\[^\\]+\\|/users/|/home/|/root/)", value):
            raise ObservationLedgerError("LEDGER_PRIVATE_ABSOLUTE_PATH_FORBIDDEN")


@dataclass(frozen=True)
class TrialSession:
    ledger: "UtilityObservationLedger"
    study_id: str
    pair_id: str
    trial_id: str
    condition: str
    opened_monotonic_ns: int

    def elapsed_ms(self, now_ns: int | None = None) -> int:
        end = self.ledger._monotonic_ns() if now_ns is None else now_ns
        return max(0, (end - self.opened_monotonic_ns) // 1_000_000)

    def prepare_intervention(self, **payload) -> dict:
        return self.ledger.append_event(
            study_id=self.study_id, pair_id=self.pair_id, trial_id=self.trial_id,
            event_type="INTERVENTION_PREPARED", payload=payload,
        )

    def record_acceptance(self, **payload) -> dict:
        return self.ledger.append_event(
            study_id=self.study_id, pair_id=self.pair_id, trial_id=self.trial_id,
            event_type="ACCEPTANCE_RECORDED", payload=payload,
        )

    def complete(self) -> dict:
        events = self.ledger.trial_events(self.study_id, self.trial_id)
        accepted = next((item for item in reversed(events) if item["event_type"] == "ACCEPTANCE_RECORDED"), None)
        hosts = [item for item in events if item["event_type"] == "HOST_INVOCATION_COMPLETED"]
        if not accepted or not hosts:
            raise ObservationLedgerError("LEDGER_TRIAL_COMPLETION_PREREQUISITES_MISSING")
        # Never accept a caller-supplied duration: the ledger derives the
        # neutral wall clock from TRIAL_OPENED through acceptance completion.
        elapsed = self.elapsed_ms()
        host_durations = [item["payload"]["host_process_elapsed_ms"] for item in hosts]
        host_elapsed = sum(host_durations) if all(value is not None for value in host_durations) else None
        if accepted["payload"]["retry_count"] != len(hosts) - 1:
            raise ObservationLedgerError("LEDGER_RETRY_COUNT_MISMATCH")
        if type(elapsed) is not int or elapsed < 0 or (host_elapsed is not None and elapsed < host_elapsed):
            raise ObservationLedgerError("LEDGER_TRIAL_WALL_TIME_INVALID")
        verdict = accepted["payload"]["verdict"]
        if verdict == "INCONCLUSIVE":
            return self.ledger.append_event(
                study_id=self.study_id, pair_id=self.pair_id, trial_id=self.trial_id,
                event_type="TRIAL_INCONCLUSIVE", payload={
                    "reason_code": accepted["payload"]["inconclusive_reason"] or "ACCEPTANCE_INCOMPLETE",
                    "trial_wall_elapsed_ms": elapsed, "host_process_elapsed_ms": host_elapsed,
                    "evidence_refs": accepted["payload"]["evidence_refs"],
                },
            )
        return self.ledger.append_event(
            study_id=self.study_id, pair_id=self.pair_id, trial_id=self.trial_id,
            event_type="TRIAL_COMPLETED", payload={
                "verdict": verdict, "first_pass": accepted["payload"]["first_pass"],
                "retry_count": accepted["payload"]["retry_count"],
                "trial_wall_elapsed_ms": elapsed, "host_process_elapsed_ms": host_elapsed,
                "telemetry_refs": [], "unknowns": ["MODEL_VISIBLE", "SKILL_USED", "PROVIDER_COST"],
            },
        )

    def inconclusive(self, reason_code: str, *, host_process_elapsed_ms: int | None = None) -> dict:
        if reason_code not in REASON_CODES:
            raise ObservationLedgerError("LEDGER_REASON_CODE_INVALID")
        hosts = [item for item in self.ledger.trial_events(self.study_id, self.trial_id)
                 if item["event_type"] == "HOST_INVOCATION_COMPLETED"]
        durations = [item["payload"]["host_process_elapsed_ms"] for item in hosts]
        if hosts:
            host_elapsed = sum(durations) if all(value is not None for value in durations) else None
        else:
            host_elapsed = host_process_elapsed_ms
        return self.ledger.append_event(
            study_id=self.study_id, pair_id=self.pair_id, trial_id=self.trial_id,
            event_type="TRIAL_INCONCLUSIVE", payload={
                "reason_code": reason_code, "trial_wall_elapsed_ms": self.elapsed_ms(),
                "host_process_elapsed_ms": host_elapsed, "evidence_refs": [],
            },
        )


class UtilityObservationLedger:
    """Append-only JSONL ledger kept outside both repository and Nexus data root."""

    def __init__(self, path: str | Path, *, repository_root: str | Path | None = None,
                 clock: Callable[[], str] = _utc_now,
                 monotonic_ns: Callable[[], int] | None = None):
        self.path = Path(path).expanduser().resolve()
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.repository_root = Path(repository_root).resolve() if repository_root else Path(__file__).resolve().parents[2]
        self._clock = clock
        if monotonic_ns is None:
            import time
            monotonic_ns = time.monotonic_ns
        self._monotonic_ns = monotonic_ns
        self._thread_lock = threading.RLock()
        self._schema = json.loads(_EVENT_SCHEMA_PATH.read_text(encoding="utf-8"))
        self._schema_validator = Draft202012Validator(self._schema)
        self._assert_external(self.path)
        self._assert_external(self.lock_path)

    def _assert_external(self, path: Path) -> None:
        try:
            path.resolve().relative_to(self.repository_root)
        except ValueError:
            return
        raise ObservationLedgerError("LEDGER_MUST_BE_REPO_EXTERNAL")

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._thread_lock, self.lock_path.open("a+b") as stream:
            if os.name == "nt":
                import msvcrt
                stream.seek(0, os.SEEK_END)
                if stream.tell() == 0:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _read_unlocked(self) -> list[dict]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise ObservationLedgerError("LEDGER_TORN_OR_PARTIAL_FINAL_EVENT")
        events = []
        previous = None
        for expected_sequence, line in enumerate(raw.splitlines(), start=1):
            try:
                event = json.loads(line.decode("utf-8", errors="strict"), object_pairs_hook=_reject_duplicate_pairs)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ObservationLedgerError("LEDGER_EVENT_JSON_INVALID") from exc
            errors = sorted(self._schema_validator.iter_errors(event), key=lambda item: list(item.path))
            if errors:
                raise ObservationLedgerError("LEDGER_EVENT_SCHEMA_INVALID")
            if event["sequence"] != expected_sequence or event["previous_event_sha256"] != previous:
                raise ObservationLedgerError("LEDGER_EVENT_CHAIN_INVALID")
            body = {key: value for key, value in event.items() if key != "event_sha256"}
            if sha256_bytes(canonical_json(body)) != event["event_sha256"]:
                raise ObservationLedgerError("LEDGER_EVENT_HASH_INVALID")
            _assert_no_private_path_or_body(event)
            previous = event["event_sha256"]
            events.append(event)
        self._validate_history(events)
        return events

    def events(self) -> list[dict]:
        with self._locked():
            return self._read_unlocked()

    def trial_events(self, study_id: str, trial_id: str) -> list[dict]:
        return [event for event in self.events()
                if event["study_id"] == study_id and event["trial_id"] == trial_id]

    def open_trial(self, *, study_id: str, pair_id: str, trial_id: str,
                   assigned_condition: str, task_card_sha256: str,
                   task_variant_sha256: str, task_payload_sha256: str,
                   starting_commit: str, environment_snapshot_ref: str | None,
                   environment_snapshot_sha256: str | None,
                   acceptance_rubric_ref: str, acceptance_rubric_sha256: str,
                   nexus_task_id: str | None = None, nexus_run_id: str | None = None,
                   unknowns: list[str] | None = None) -> TrialSession:
        opened_ns = self._monotonic_ns()
        payload = {
            "assigned_condition": assigned_condition,
            "task_card_sha256": task_card_sha256,
            "task_variant_sha256": task_variant_sha256,
            "task_payload_sha256": task_payload_sha256,
            "starting_commit": starting_commit,
            "environment_snapshot_ref": environment_snapshot_ref,
            "environment_snapshot_sha256": environment_snapshot_sha256,
            "host_surface": "CODEX_CLI",
            "acceptance_rubric_ref": acceptance_rubric_ref,
            "acceptance_rubric_sha256": acceptance_rubric_sha256,
            "nexus_task_id": nexus_task_id,
            "nexus_run_id": nexus_run_id,
            "unknowns": sorted(set(unknowns or ["MODEL_VISIBLE", "HOST_MODEL_IDENTITY", "PROVIDER_COST"])),
        }
        self.append_event(study_id=study_id, pair_id=pair_id, trial_id=trial_id,
                          event_type="TRIAL_OPENED", payload=payload)
        return TrialSession(self, study_id, pair_id, trial_id, assigned_condition, opened_ns)

    def append_event(self, *, study_id: str, pair_id: str, trial_id: str,
                     event_type: str, payload: dict) -> dict:
        if event_type not in EVENT_TYPES:
            raise ObservationLedgerError("LEDGER_EVENT_TYPE_INVALID")
        for value, field in ((study_id, "study_id"), (pair_id, "pair_id"), (trial_id, "trial_id")):
            _safe_id(value, field)
        if not isinstance(payload, dict):
            raise ObservationLedgerError("LEDGER_PAYLOAD_INVALID")
        _assert_no_private_path_or_body(payload)
        with self._locked():
            events = self._read_unlocked()
            self._validate_new_event(events, study_id, pair_id, trial_id, event_type, payload)
            sequence = len(events) + 1
            previous = events[-1]["event_sha256"] if events else None
            body = {
                "schema_id": SCHEMA_ID, "schema_version": SCHEMA_VERSION,
                "event_id": f"{study_id}:{trial_id}:{sequence}",
                "study_id": study_id, "pair_id": pair_id, "trial_id": trial_id,
                "sequence": sequence, "event_type": event_type,
                "recorded_at": self._clock(), "provenance": _event_provenance(event_type, payload),
                "payload": payload, "previous_event_sha256": previous,
            }
            event = {**body, "event_sha256": sha256_bytes(canonical_json(body))}
            errors = list(self._schema_validator.iter_errors(event))
            if errors:
                raise ObservationLedgerError("LEDGER_EVENT_SCHEMA_INVALID")
            _assert_no_private_path_or_body(event)
            encoded = canonical_json(event) + b"\n"
            with self.path.open("ab") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            return event

    def _validate_new_event(self, events, study_id, pair_id, trial_id, event_type, payload) -> None:
        _validate_payload(event_type, payload)
        history = [event for event in events if event["study_id"] == study_id and event["trial_id"] == trial_id]
        if event_type == "TRIAL_OPENED":
            if history:
                raise ObservationLedgerError("LEDGER_DUPLICATE_TRIAL_ID")
            opened_pairs = [event for event in events if event["study_id"] == study_id and event["pair_id"] == pair_id
                            and event["event_type"] == "TRIAL_OPENED"]
            if any(event["payload"]["assigned_condition"] == payload.get("assigned_condition") for event in opened_pairs):
                raise ObservationLedgerError("LEDGER_DUPLICATE_PAIR_CONDITION")
            pair_fields = ("task_card_sha256", "starting_commit", "environment_snapshot_ref",
                           "environment_snapshot_sha256", "acceptance_rubric_ref", "acceptance_rubric_sha256")
            for paired in opened_pairs:
                if any(paired["payload"].get(field) != payload.get(field) for field in pair_fields):
                    raise ObservationLedgerError("LEDGER_MATCHED_PAIR_IDENTITY_MISMATCH")
            if payload["assigned_condition"] == "HOST_NATIVE_BYPASS" and (
                payload["nexus_task_id"] is not None or payload["nexus_run_id"] is not None
            ):
                raise ObservationLedgerError("LEDGER_A_CONDITION_MUST_NOT_HAVE_NEXUS_RUN")
            if payload["assigned_condition"] == "NEXUS_ACTIVE" and (payload["nexus_task_id"] is None) != (payload["nexus_run_id"] is None):
                raise ObservationLedgerError("LEDGER_B_NEXUS_TASK_RUN_PAIR_INVALID")
            return
        if not history or history[0]["event_type"] != "TRIAL_OPENED":
            raise ObservationLedgerError("LEDGER_TRIAL_NOT_OPEN")
        if history[-1]["event_type"] in {"TRIAL_COMPLETED", "TRIAL_INCONCLUSIVE", "TRIAL_EXCLUDED", "TRIAL_INVALIDATED"}:
            if event_type == "TRIAL_INVALIDATED" and history[-1]["event_type"] != "TRIAL_INVALIDATED":
                return
            raise ObservationLedgerError("LEDGER_TRIAL_ALREADY_TERMINAL")
        opened = history[0]["payload"]
        condition = opened["assigned_condition"]
        previous_type = history[-1]["event_type"]
        allowed = {
            "TRIAL_OPENED": {"INTERVENTION_PREPARED", "HOST_INVOCATION_STARTED", "TRIAL_INCONCLUSIVE", "TRIAL_EXCLUDED", "TRIAL_INVALIDATED"},
            "INTERVENTION_PREPARED": {"HOST_INVOCATION_STARTED", "TRIAL_INCONCLUSIVE", "TRIAL_EXCLUDED", "TRIAL_INVALIDATED"},
            "HOST_INVOCATION_STARTED": {"HOST_INVOCATION_COMPLETED", "TRIAL_INCONCLUSIVE", "TRIAL_EXCLUDED", "TRIAL_INVALIDATED"},
            "HOST_INVOCATION_COMPLETED": {"INTERVENTION_PREPARED", "HOST_INVOCATION_STARTED", "ACCEPTANCE_RECORDED", "TRIAL_INCONCLUSIVE", "TRIAL_EXCLUDED", "TRIAL_INVALIDATED"},
            "ACCEPTANCE_RECORDED": {"TRIAL_COMPLETED", "TRIAL_INCONCLUSIVE", "TRIAL_EXCLUDED", "TRIAL_INVALIDATED"},
        }
        if event_type not in allowed.get(previous_type, set()):
            raise ObservationLedgerError("LEDGER_ILLEGAL_EVENT_TRANSITION")
        if condition == "NEXUS_ACTIVE":
            if event_type == "INTERVENTION_PREPARED" and payload.get("preparation_status") not in INTERVENTION_PREPARATION_STATUSES:
                raise ObservationLedgerError("LEDGER_INTERVENTION_STATUS_INVALID")
            if event_type == "INTERVENTION_PREPARED":
                if payload["task_payload_sha256"] != opened["task_payload_sha256"]:
                    raise ObservationLedgerError("LEDGER_HOST_INPUT_BINDING_MISMATCH")
                if payload["preparation_status"] == "PREPARED_NOT_SUBMITTED" and (
                    payload["nexus_task_id"] is None or payload["nexus_run_id"] is None
                    or payload["final_cli_input_sha256"] is None
                ):
                    raise ObservationLedgerError("LEDGER_B_INTERVENTION_BINDING_INCOMPLETE")
                if opened["nexus_task_id"] is not None and payload["nexus_task_id"] != opened["nexus_task_id"]:
                    raise ObservationLedgerError("LEDGER_B_TASK_BINDING_MISMATCH")
                if opened["nexus_run_id"] is not None and payload["nexus_run_id"] != opened["nexus_run_id"]:
                    raise ObservationLedgerError("LEDGER_B_RUN_BINDING_MISMATCH")
            if event_type == "HOST_INVOCATION_STARTED":
                prepared_event = next((item for item in reversed(history) if item["event_type"] == "INTERVENTION_PREPARED"), None)
                prepared = prepared_event["payload"] if prepared_event else None
                if not prepared or prepared["preparation_status"] != "PREPARED_NOT_SUBMITTED":
                    raise ObservationLedgerError("LEDGER_B_INTERVENTION_NOT_SUBMITTED")
                if payload["controller_invocation_id"] != prepared["controller_invocation_id"]:
                    raise ObservationLedgerError("LEDGER_INTERVENTION_INVOCATION_MISMATCH")
            if event_type == "INTERVENTION_PREPARED" and previous_type == "HOST_INVOCATION_COMPLETED":
                previous_prepared = next((item["payload"] for item in reversed(history)
                                          if item["event_type"] == "INTERVENTION_PREPARED"), None)
                if previous_prepared:
                    immutable_keys = {
                        "preparation_status", "boundary", "composition_version", "task_payload_sha256",
                        "final_cli_input_sha256", "final_cli_input_bytes", "context_payload_sha256",
                        "context_pack_refs", "context_pack_byte_size", "skill_payload_sha256",
                        "skill_resolution_ref", "skill_resolution_status", "skill_resolution", "skill_candidate_count",
                        "selected_skill_ref", "host_skill_availability", "host_inventory_provenance",
                        "skill_instruction_load_status", "skill_instruction_byte_size", "skill_selection_latency_ms",
                        "skill_instruction_object_ref",
                        "nexus_task_id", "nexus_run_id",
                    }
                    if any(previous_prepared.get(key) != payload.get(key) for key in immutable_keys):
                        raise ObservationLedgerError("LEDGER_RETRY_INPUT_MISMATCH")
        elif event_type == "INTERVENTION_PREPARED":
            raise ObservationLedgerError("LEDGER_A_CONDITION_CANNOT_PREPARE_NEXUS_INTERVENTION")
        if event_type == "HOST_INVOCATION_STARTED":
            if payload["input_sha256"] != (history[-1]["payload"].get("final_cli_input_sha256")
                                           if previous_type == "INTERVENTION_PREPARED"
                                           else opened["task_payload_sha256"]):
                raise ObservationLedgerError("LEDGER_HOST_INPUT_BINDING_MISMATCH")
            if previous_type == "INTERVENTION_PREPARED" and payload["input_bytes"] != history[-1]["payload"]["final_cli_input_bytes"]:
                raise ObservationLedgerError("LEDGER_HOST_INPUT_BYTE_LENGTH_MISMATCH")
            starts = [item for item in history if item["event_type"] == "HOST_INVOCATION_STARTED"]
            if payload["attempt_number"] != len(starts) + 1:
                raise ObservationLedgerError("LEDGER_ATTEMPT_NUMBER_INVALID")
            all_invocations = [item for item in events if item["event_type"] == "HOST_INVOCATION_STARTED"]
            if any(item["payload"]["controller_invocation_id"] == payload["controller_invocation_id"] for item in all_invocations):
                raise ObservationLedgerError("LEDGER_HOST_INVOCATION_ID_REUSED")
            if starts:
                first_input = starts[0]["payload"]["input_sha256"]
                if payload["input_sha256"] != first_input:
                    raise ObservationLedgerError("LEDGER_RETRY_INPUT_MISMATCH")
        if event_type == "HOST_INVOCATION_COMPLETED":
            started = next((item["payload"] for item in reversed(history) if item["event_type"] == "HOST_INVOCATION_STARTED"), None)
            if (not started or payload["controller_invocation_id"] != started["controller_invocation_id"]
                    or payload["attempt_number"] != started["attempt_number"]):
                raise ObservationLedgerError("LEDGER_HOST_INVOCATION_ID_MISMATCH")
            if payload["input_submission_status"] == "CONTROLLER_SUBMITTED_TO_CODEX_CLI":
                if (payload["submitted_input_sha256"] != started["input_sha256"]
                        or payload["submitted_input_bytes"] != started["input_bytes"]):
                    raise ObservationLedgerError("LEDGER_SUBMITTED_INPUT_BINDING_MISMATCH")
                expected_intervention = (
                    "EVAL_INTERVENTION_TRANSPORT" if opened["assigned_condition"] == "NEXUS_ACTIVE" else "NONE"
                )
                if payload["received_intervention"] != expected_intervention:
                    raise ObservationLedgerError("LEDGER_RECEIVED_INTERVENTION_BINDING_MISMATCH")
            elif payload["submitted_input_sha256"] is not None or payload["submitted_input_bytes"] is not None:
                raise ObservationLedgerError("LEDGER_UNCONFIRMED_SUBMISSION_HAS_INPUT_BINDING")
            elif payload["received_intervention"] != "UNCONFIRMED":
                raise ObservationLedgerError("LEDGER_UNCONFIRMED_INTERVENTION_STATUS_MISMATCH")
            if payload["thread_started"] != (payload["thread_id"] is not None):
                raise ObservationLedgerError("LEDGER_THREAD_START_IDENTITY_MISMATCH")
            if (payload["agent_output_sha256"] is None) != (payload["agent_output_bytes"] is None):
                raise ObservationLedgerError("LEDGER_AGENT_OUTPUT_IDENTITY_MISMATCH")
        if event_type == "ACCEPTANCE_RECORDED":
            if (payload["rubric_ref"] != opened["acceptance_rubric_ref"]
                    or payload["rubric_sha256"] != opened["acceptance_rubric_sha256"]):
                raise ObservationLedgerError("LEDGER_ACCEPTANCE_RUBRIC_MISMATCH")
            host = next((item["payload"] for item in reversed(history)
                         if item["event_type"] == "HOST_INVOCATION_COMPLETED"), None)
            if (not host or payload["evaluated_output_sha256"] != host["agent_output_sha256"]
                    or host["exit_code"] != 0 or host["timed_out"] or not host["thread_started"]
                    or not host["turn_completed"] or host["event_parse_status"] != "COMPLETE"
                    or host["thread_id_reused"] or host["agent_output_sha256"] is None):
                raise ObservationLedgerError("LEDGER_ACCEPTANCE_OUTPUT_MISMATCH")
        if event_type == "TRIAL_COMPLETED":
            acceptance = next((item["payload"] for item in reversed(history) if item["event_type"] == "ACCEPTANCE_RECORDED"), None)
            hosts = [item["payload"] for item in history if item["event_type"] == "HOST_INVOCATION_COMPLETED"]
            if not acceptance or not hosts or payload["verdict"] != acceptance["verdict"]:
                raise ObservationLedgerError("LEDGER_TRIAL_COMPLETION_BINDING_MISMATCH")
            if acceptance["retry_count"] != len(hosts) - 1:
                raise ObservationLedgerError("LEDGER_RETRY_COUNT_MISMATCH")
            durations = [host["host_process_elapsed_ms"] for host in hosts]
            host_total = sum(durations) if all(value is not None for value in durations) else None
            if host_total is not None and payload["trial_wall_elapsed_ms"] < host_total:
                raise ObservationLedgerError("LEDGER_TRIAL_WALL_TIME_INVALID")

    def _validate_history(self, events: list[dict]) -> None:
        trial_ids = set()
        pair_conditions = set()
        histories: dict[tuple[str, str], list[dict]] = {}
        for event in events:
            key = (event["study_id"], event["trial_id"])
            if event["event_type"] == "TRIAL_OPENED":
                if key in trial_ids:
                    raise ObservationLedgerError("LEDGER_DUPLICATE_TRIAL_ID")
                trial_ids.add(key)
                pair_key = (event["study_id"], event["pair_id"], event["payload"]["assigned_condition"])
                if pair_key in pair_conditions:
                    raise ObservationLedgerError("LEDGER_DUPLICATE_PAIR_CONDITION")
                same_pair = [prior for prior in events if prior["study_id"] == event["study_id"]
                             and prior["pair_id"] == event["pair_id"] and prior["event_type"] == "TRIAL_OPENED"]
                pair_fields = ("task_card_sha256", "starting_commit", "environment_snapshot_ref",
                               "environment_snapshot_sha256", "acceptance_rubric_ref", "acceptance_rubric_sha256")
                if any(any(prior["payload"].get(field) != event["payload"].get(field) for field in pair_fields)
                       for prior in same_pair):
                    raise ObservationLedgerError("LEDGER_MATCHED_PAIR_IDENTITY_MISMATCH")
                pair_conditions.add(pair_key)
            prior = histories.get(key, [])
            try:
                # Reuse transition validation without changing the stored chain.
                self._validate_new_event(prior, event["study_id"], event["pair_id"], event["trial_id"],
                                         event["event_type"], event["payload"])
            except ObservationLedgerError:
                raise
            histories.setdefault(key, []).append(event)


def _event_provenance(event_type: str, payload: dict) -> dict:
    base = {"event_timestamp": "OBSERVED", "event_payload": "OBSERVED"}
    if event_type == "TRIAL_OPENED":
        base["assigned_condition"] = "DERIVED"
        base["task_identity"] = "DERIVED"
    if event_type == "HOST_INVOCATION_STARTED":
        base["cli_version"] = payload["cli_version_provenance"]
        base["cli_input_boundary"] = "OBSERVED"
    if event_type == "HOST_INVOCATION_COMPLETED":
        base["host_usage"] = "HOST_DECLARED" if any(value["provenance"] == "HOST_DECLARED" for value in payload["usage"].values()) else "UNAVAILABLE"
        base["host_model_identity"] = "UNAVAILABLE"
    if event_type == "TRIAL_COMPLETED":
        base["trial_wall_elapsed_ms"] = "DERIVED"
        base["host_process_elapsed_ms"] = "OBSERVED"
    return base


def _metric(value: int | None) -> dict:
    return {"value": value, "provenance": "UNAVAILABLE" if value is None else "HOST_DECLARED"}


def unavailable_host_usage() -> dict:
    return {name: _metric(None) for name in (
        "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
        "output_tokens", "reasoning_output_tokens",
    )}


def _validate_payload(event_type: str, payload: dict) -> None:
    # Schema validation is paired with semantic checks here so no event accepts
    # free-form prompt, response, paths, or tool-argument fields.
    if event_type == "TRIAL_OPENED":
        required = {"assigned_condition", "task_card_sha256", "task_variant_sha256", "task_payload_sha256",
                    "starting_commit", "environment_snapshot_ref", "environment_snapshot_sha256", "host_surface",
                    "acceptance_rubric_ref", "acceptance_rubric_sha256", "nexus_task_id", "nexus_run_id", "unknowns"}
        if set(payload) != required or payload["assigned_condition"] not in CONDITIONS or payload["host_surface"] != "CODEX_CLI":
            raise ObservationLedgerError("LEDGER_TRIAL_OPENED_PAYLOAD_INVALID")
        for key in ("task_card_sha256", "task_variant_sha256", "task_payload_sha256", "acceptance_rubric_sha256"):
            if not isinstance(payload[key], str) or not _SHA_RE.fullmatch(payload[key]):
                raise ObservationLedgerError("LEDGER_SHA256_INVALID")
        if not _COMMIT_RE.fullmatch(payload["starting_commit"]):
            raise ObservationLedgerError("LEDGER_STARTING_COMMIT_INVALID")
        if payload["environment_snapshot_sha256"] is not None and not _SHA_RE.fullmatch(payload["environment_snapshot_sha256"]):
            raise ObservationLedgerError("LEDGER_ENVIRONMENT_HASH_INVALID")
        for key in ("environment_snapshot_ref", "acceptance_rubric_ref", "nexus_task_id", "nexus_run_id"):
            _safe_id(payload[key], key, ref=True, nullable=key in {"environment_snapshot_ref", "nexus_task_id", "nexus_run_id"})
        if not isinstance(payload["unknowns"], list) or any(not isinstance(item, str) or not _ENUM_RE.fullmatch(item) for item in payload["unknowns"]):
            raise ObservationLedgerError("LEDGER_UNKNOWNS_INVALID")
    elif event_type == "INTERVENTION_PREPARED":
        required = {"controller_invocation_id", "preparation_status", "boundary", "composition_version",
                    "task_payload_sha256", "final_cli_input_sha256", "final_cli_input_bytes",
                    "context_payload_sha256", "context_pack_refs", "context_pack_byte_size",
                    "skill_payload_sha256", "skill_resolution_ref", "skill_resolution_status", "skill_resolution",
                    "skill_candidate_count", "selected_skill_ref", "host_skill_availability",
                    "host_inventory_provenance", "skill_instruction_load_status", "skill_instruction_byte_size",
                    "skill_selection_latency_ms",
                    "skill_instruction_object_ref",
                    "nexus_task_id", "nexus_run_id", "provenance"}
        if set(payload) != required or payload["preparation_status"] not in INTERVENTION_PREPARATION_STATUSES or payload["boundary"] != "CODEX_CLI_INPUT":
            raise ObservationLedgerError("LEDGER_INTERVENTION_PAYLOAD_INVALID")
        for key in ("task_payload_sha256", "final_cli_input_sha256", "context_payload_sha256", "skill_payload_sha256"):
            if payload[key] is not None and not _SHA_RE.fullmatch(payload[key]):
                raise ObservationLedgerError("LEDGER_SHA256_INVALID")
        _safe_id(payload["controller_invocation_id"], "controller_invocation_id")
        for key in ("skill_resolution_ref", "selected_skill_ref", "skill_instruction_object_ref", "nexus_task_id", "nexus_run_id"):
            _safe_id(payload[key], key, ref=True, nullable=True)
        if payload["skill_resolution_status"] is not None and payload["skill_resolution_status"] not in SKILL_RESOLUTION_STATUSES:
            raise ObservationLedgerError("LEDGER_SKILL_RESOLUTION_STATUS_INVALID")
        if payload["skill_resolution"] is not None and payload["skill_resolution"] not in SKILL_RESOLUTIONS:
            raise ObservationLedgerError("LEDGER_SKILL_RESOLUTION_INVALID")
        if payload["skill_candidate_count"] is not None and (type(payload["skill_candidate_count"]) is not int or payload["skill_candidate_count"] < 0):
            raise ObservationLedgerError("LEDGER_SKILL_CANDIDATE_COUNT_INVALID")
        if payload["host_skill_availability"] is not None and payload["host_skill_availability"] not in HOST_SKILL_AVAILABILITY:
            raise ObservationLedgerError("LEDGER_HOST_SKILL_AVAILABILITY_INVALID")
        if payload["host_inventory_provenance"] is not None and payload["host_inventory_provenance"] not in HOST_INVENTORY_PROVENANCE:
            raise ObservationLedgerError("LEDGER_HOST_INVENTORY_PROVENANCE_INVALID")
        if payload["skill_instruction_load_status"] not in {None, "NOT_LOADED", "LOADED_TO_GOVERNED_ARTIFACT"}:
            raise ObservationLedgerError("LEDGER_SKILL_LOAD_STATUS_INVALID")
        if payload["skill_instruction_byte_size"] is not None and (
            type(payload["skill_instruction_byte_size"]) is not int or payload["skill_instruction_byte_size"] < 0
        ):
            raise ObservationLedgerError("LEDGER_SKILL_BYTE_SIZE_INVALID")
        latency = payload["skill_selection_latency_ms"]
        if latency is not None and (type(latency) not in {int, float} or not math.isfinite(latency) or latency < 0):
            raise ObservationLedgerError("LEDGER_SKILL_SELECTION_LATENCY_INVALID")
        if not isinstance(payload["context_pack_refs"], list):
            raise ObservationLedgerError("LEDGER_CONTEXT_REFS_INVALID")
        for ref in payload["context_pack_refs"]:
            _safe_id(ref, "context_pack_ref", ref=True)
        if payload["context_pack_byte_size"] is not None and (
            type(payload["context_pack_byte_size"]) is not int or payload["context_pack_byte_size"] < 0
        ):
            raise ObservationLedgerError("LEDGER_BYTE_SIZE_INVALID")
        if type(payload["final_cli_input_bytes"]) is not int or payload["final_cli_input_bytes"] < 0:
            raise ObservationLedgerError("LEDGER_BYTE_SIZE_INVALID")
        if payload["composition_version"] != "UTILITY_INTERVENTION_ENVELOPE_V1":
            raise ObservationLedgerError("LEDGER_COMPOSITION_VERSION_INVALID")
        _provenance_map(payload["provenance"])
    elif event_type == "HOST_INVOCATION_STARTED":
        required = {"controller_invocation_id", "attempt_number", "sanitized_argv", "sanitized_argv_sha256", "cli_version",
                    "cli_version_provenance", "input_sha256", "input_bytes", "subprocess_shell", "cwd_policy", "host_surface"}
        if set(payload) != required or payload["host_surface"] != "CODEX_CLI" or payload["subprocess_shell"] is not False or payload["cwd_policy"] != "FRESH_EMPTY_REPO_EXTERNAL":
            raise ObservationLedgerError("LEDGER_HOST_STARTED_PAYLOAD_INVALID")
        for key in ("sanitized_argv_sha256", "input_sha256"):
            if not _SHA_RE.fullmatch(payload[key]):
                raise ObservationLedgerError("LEDGER_SHA256_INVALID")
        if (not isinstance(payload["sanitized_argv"], list) or not payload["sanitized_argv"]
                or any(not isinstance(arg, str) or not _ARGV_TOKEN_RE.fullmatch(arg) for arg in payload["sanitized_argv"])):
            raise ObservationLedgerError("LEDGER_SANITIZED_ARGV_INVALID")
        if tuple(payload["sanitized_argv"]) != FROZEN_SANITIZED_CODEX_ARGV:
            raise ObservationLedgerError("LEDGER_OFFICIAL_ARGV_MISMATCH")
        if sha256_bytes(canonical_json(payload["sanitized_argv"])) != payload["sanitized_argv_sha256"]:
            raise ObservationLedgerError("LEDGER_ARGV_HASH_MISMATCH")
        _safe_id(payload["controller_invocation_id"], "controller_invocation_id")
        if type(payload["attempt_number"]) is not int or payload["attempt_number"] < 1:
            raise ObservationLedgerError("LEDGER_ATTEMPT_NUMBER_INVALID")
        if payload["cli_version_provenance"] not in PROVENANCE:
            raise ObservationLedgerError("LEDGER_CLI_VERSION_PROVENANCE_INVALID")
        if payload["cli_version"] is not None and (not isinstance(payload["cli_version"], str) or len(payload["cli_version"]) > 64 or not re.fullmatch(r"[A-Za-z0-9 .+_-]+", payload["cli_version"])):
            raise ObservationLedgerError("LEDGER_CLI_VERSION_INVALID")
        if type(payload["input_bytes"]) is not int or payload["input_bytes"] < 0:
            raise ObservationLedgerError("LEDGER_BYTE_SIZE_INVALID")
    elif event_type == "HOST_INVOCATION_COMPLETED":
        required = {"controller_invocation_id", "attempt_number", "exit_code", "timed_out", "thread_started", "thread_id",
                    "turn_completed", "agent_output_sha256", "agent_output_bytes", "host_process_elapsed_ms",
                    "event_parse_status", "malformed_line_count", "unknown_event_count", "thread_id_reused",
                    "tool_activity", "usage", "usage_basis", "stderr_bytes", "stderr_content_status",
                    "input_submission_status", "submitted_input_sha256", "submitted_input_bytes", "received_intervention"}
        if set(payload) != required:
            raise ObservationLedgerError("LEDGER_HOST_COMPLETED_PAYLOAD_INVALID")
        _safe_id(payload["controller_invocation_id"], "controller_invocation_id")
        if type(payload["attempt_number"]) is not int or payload["attempt_number"] < 1:
            raise ObservationLedgerError("LEDGER_ATTEMPT_NUMBER_INVALID")
        _safe_id(payload["thread_id"], "thread_id", nullable=True)
        for key in ("agent_output_sha256",):
            if payload[key] is not None and not _SHA_RE.fullmatch(payload[key]):
                raise ObservationLedgerError("LEDGER_SHA256_INVALID")
        for key in ("timed_out", "thread_started", "turn_completed", "thread_id_reused"):
            if type(payload[key]) is not bool:
                raise ObservationLedgerError("LEDGER_BOOLEAN_INVALID")
        for key in ("agent_output_bytes", "host_process_elapsed_ms", "malformed_line_count", "unknown_event_count", "stderr_bytes"):
            if payload[key] is not None and (type(payload[key]) is not int or payload[key] < 0):
                raise ObservationLedgerError("LEDGER_INTEGER_INVALID")
        if payload["exit_code"] is not None and type(payload["exit_code"]) is not int:
            raise ObservationLedgerError("LEDGER_EXIT_CODE_INVALID")
        if payload["event_parse_status"] not in {"COMPLETE", "INCOMPLETE"} or payload["stderr_content_status"] not in {"EMPTY", "NONEMPTY", "UNAVAILABLE"}:
            raise ObservationLedgerError("LEDGER_HOST_STATUS_INVALID")
        if payload["usage_basis"] not in {"HOST_REPORTED_TOTAL_TURN_TOKEN_TELEMETRY", "UNAVAILABLE"}:
            raise ObservationLedgerError("LEDGER_USAGE_BASIS_INVALID")
        if payload["input_submission_status"] not in INPUT_SUBMISSION_STATUSES:
            raise ObservationLedgerError("LEDGER_INPUT_SUBMISSION_STATUS_INVALID")
        if payload["received_intervention"] not in {"EVAL_INTERVENTION_TRANSPORT", "NONE", "UNCONFIRMED"}:
            raise ObservationLedgerError("LEDGER_RECEIVED_INTERVENTION_STATUS_INVALID")
        if payload["submitted_input_sha256"] is not None and not _SHA_RE.fullmatch(payload["submitted_input_sha256"]):
            raise ObservationLedgerError("LEDGER_SUBMITTED_INPUT_HASH_INVALID")
        if payload["submitted_input_bytes"] is not None and (type(payload["submitted_input_bytes"]) is not int or payload["submitted_input_bytes"] < 0):
            raise ObservationLedgerError("LEDGER_SUBMITTED_INPUT_SIZE_INVALID")
        if not isinstance(payload["tool_activity"], list) or not isinstance(payload["usage"], dict):
            raise ObservationLedgerError("LEDGER_HOST_SUMMARY_INVALID")
        for item in payload["tool_activity"]:
            if set(item) != {"event_type", "count"} or item["event_type"] not in {"command_execution", "mcp_tool_call", "web_search_call", "file_search_call", "computer_call", "unknown_tool_event"} or type(item["count"]) is not int or item["count"] < 0:
                raise ObservationLedgerError("LEDGER_TOOL_ACTIVITY_INVALID")
        if set(payload["usage"]) != set(unavailable_host_usage()):
            raise ObservationLedgerError("LEDGER_HOST_USAGE_INVALID")
        for name, metric in payload["usage"].items():
            if set(metric) != {"value", "provenance"} or metric["provenance"] not in PROVENANCE:
                raise ObservationLedgerError("LEDGER_HOST_USAGE_INVALID")
            if metric["value"] is not None and (type(metric["value"]) is not int or metric["value"] < 0):
                raise ObservationLedgerError("LEDGER_HOST_USAGE_INVALID")
            if (metric["value"] is None) != (metric["provenance"] == "UNAVAILABLE"):
                raise ObservationLedgerError("LEDGER_HOST_USAGE_PROVENANCE_INVALID")
        has_usage = any(item["value"] is not None for item in payload["usage"].values())
        if has_usage != (payload["usage_basis"] == "HOST_REPORTED_TOTAL_TURN_TOKEN_TELEMETRY"):
            raise ObservationLedgerError("LEDGER_USAGE_BASIS_MISMATCH")
    elif event_type == "ACCEPTANCE_RECORDED":
        required = {"rubric_ref", "rubric_sha256", "evaluator_kind", "evaluated_output_sha256", "verdict", "evidence_refs",
                    "evidence_sha256", "first_pass", "retry_count", "inconclusive_reason"}
        if set(payload) != required or payload["evaluator_kind"] not in {"DETERMINISTIC", "BLINDED_HUMAN", "INDEPENDENT_REVIEWER"}:
            raise ObservationLedgerError("LEDGER_ACCEPTANCE_PAYLOAD_INVALID")
        _safe_id(payload["rubric_ref"], "rubric_ref", ref=True)
        if not _SHA_RE.fullmatch(payload["rubric_sha256"]):
            raise ObservationLedgerError("LEDGER_SHA256_INVALID")
        if not _SHA_RE.fullmatch(payload["evaluated_output_sha256"]):
            raise ObservationLedgerError("LEDGER_EVALUATED_OUTPUT_HASH_INVALID")
        if payload["verdict"] not in {"PASS", "FAIL", "INCONCLUSIVE"}:
            raise ObservationLedgerError("LEDGER_ACCEPTANCE_VERDICT_INVALID")
        if payload["first_pass"] is not None and type(payload["first_pass"]) is not bool:
            raise ObservationLedgerError("LEDGER_FIRST_PASS_INVALID")
        if payload["retry_count"] is not None and (type(payload["retry_count"]) is not int or payload["retry_count"] < 0):
            raise ObservationLedgerError("LEDGER_RETRY_COUNT_INVALID")
        if not isinstance(payload["evidence_refs"], list) or not isinstance(payload["evidence_sha256"], list):
            raise ObservationLedgerError("LEDGER_ACCEPTANCE_EVIDENCE_INVALID")
        if len(payload["evidence_refs"]) != len(payload["evidence_sha256"]) or any(not _SHA_RE.fullmatch(value) for value in payload["evidence_sha256"]):
            raise ObservationLedgerError("LEDGER_ACCEPTANCE_EVIDENCE_INVALID")
        for ref in payload["evidence_refs"]:
            _safe_id(ref, "acceptance_evidence_ref", ref=True)
        if payload["inconclusive_reason"] is not None and payload["inconclusive_reason"] not in REASON_CODES:
            raise ObservationLedgerError("LEDGER_REASON_CODE_INVALID")
        if payload["verdict"] == "INCONCLUSIVE" and payload["inconclusive_reason"] is None:
            raise ObservationLedgerError("LEDGER_ACCEPTANCE_INCONCLUSIVE_REASON_REQUIRED")
    elif event_type == "TRIAL_COMPLETED":
        required = {"verdict", "first_pass", "retry_count", "trial_wall_elapsed_ms", "host_process_elapsed_ms", "telemetry_refs", "unknowns"}
        if set(payload) != required or payload["verdict"] not in {"PASS", "FAIL"}:
            raise ObservationLedgerError("LEDGER_TRIAL_COMPLETED_PAYLOAD_INVALID")
        if type(payload["first_pass"]) is not bool or type(payload["retry_count"]) is not int or payload["retry_count"] < 0:
            raise ObservationLedgerError("LEDGER_TRIAL_COMPLETED_RETRY_INVALID")
        if type(payload["trial_wall_elapsed_ms"]) is not int or payload["trial_wall_elapsed_ms"] < 0:
            raise ObservationLedgerError("LEDGER_TRIAL_WALL_TIME_INVALID")
        if payload["host_process_elapsed_ms"] is not None and (type(payload["host_process_elapsed_ms"]) is not int or payload["host_process_elapsed_ms"] < 0):
            raise ObservationLedgerError("LEDGER_HOST_TIME_INVALID")
        if not isinstance(payload["telemetry_refs"], list) or not isinstance(payload["unknowns"], list):
            raise ObservationLedgerError("LEDGER_TERMINAL_LIST_INVALID")
        for ref in payload["telemetry_refs"]:
            _safe_id(ref, "telemetry_ref", ref=True)
        for unknown in payload["unknowns"]:
            if not isinstance(unknown, str) or not _ENUM_RE.fullmatch(unknown):
                raise ObservationLedgerError("LEDGER_UNKNOWN_ENUM_INVALID")
    elif event_type in {"TRIAL_INCONCLUSIVE", "TRIAL_EXCLUDED", "TRIAL_INVALIDATED"}:
        required = {"reason_code", "trial_wall_elapsed_ms", "host_process_elapsed_ms", "evidence_refs"}
        if set(payload) != required or payload["reason_code"] not in REASON_CODES:
            raise ObservationLedgerError("LEDGER_TERMINAL_REASON_INVALID")
        if payload["trial_wall_elapsed_ms"] is not None and (type(payload["trial_wall_elapsed_ms"]) is not int or payload["trial_wall_elapsed_ms"] < 0):
            raise ObservationLedgerError("LEDGER_TRIAL_WALL_TIME_INVALID")
        if payload["host_process_elapsed_ms"] is not None and (type(payload["host_process_elapsed_ms"]) is not int or payload["host_process_elapsed_ms"] < 0):
            raise ObservationLedgerError("LEDGER_HOST_TIME_INVALID")
        if not isinstance(payload["evidence_refs"], list):
            raise ObservationLedgerError("LEDGER_TERMINAL_EVIDENCE_INVALID")
        for ref in payload["evidence_refs"]:
            _safe_id(ref, "evidence_ref", ref=True)
