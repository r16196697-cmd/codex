"""HUMAN-authorized, replayable finish for one existing daily Task Root."""

from __future__ import annotations

import json
import re
from pathlib import Path, PureWindowsPath
from typing import Any, Callable

from kernel.authority.errors import AuthorizationDenied, InvalidDelegation
from kernel.object.errors import CommandConflict
from kernel.run.errors import InvalidRunTransition, TraceAdmissionDenied
from kernel.runtime.errors import RuntimeDenied
from kernel.participation import NexusParticipationMode, ParticipationModeService
from kernel.runtime.modes import RuntimeModeService


PROTOCOL_VERSION = "nexus.daily_task_finish@1"
_LOGICAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_OUTCOMES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})
_TERMINAL_RUN = {"SUCCEEDED": "SUCCEEDED", "FAILED": "FAILED", "CANCELLED": "CANCELLED"}
_TERMINAL_TASK = dict(_TERMINAL_RUN)
_NONTERMINAL_CHILD = frozenset({"CREATED", "READY", "RUNNING", "WAITING", "VERIFYING"})
_CLASS_KEYS = {"SUCCEEDED": {"verifying_event", "terminal_event"},
               "FAILED": {"terminal_event"}, "CANCELLED": {"terminal_event"}}


class DailyTaskFinishError(Exception):
    """Stable, sanitized operator-facing reason code."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


def _fail(reason: str) -> None:
    raise DailyTaskFinishError(reason)


def _pairs_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DAILY_TASK_FINISH_PLAN_INVALID")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("DAILY_TASK_FINISH_PLAN_INVALID")


def read_task_finish_plan(path: str | Path) -> dict[str, Any]:
    """Read process-local strict UTF-8 JSON; never persist or report its path."""
    try:
        value = json.loads(
            Path(path).expanduser().read_bytes().decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs_no_duplicates,
            parse_constant=_reject_constant,
        )
    except Exception:
        raise DailyTaskFinishError("DAILY_TASK_FINISH_PLAN_INVALID") from None
    if not isinstance(value, dict):
        _fail("DAILY_TASK_FINISH_PLAN_INVALID")
    return value


def _exact_object(value: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        _fail("DAILY_TASK_FINISH_PLAN_INVALID")
    return value


def _logical_id(value: Any) -> bool:
    if not isinstance(value, str) or value in {".", ".."} or _LOGICAL_ID.fullmatch(value) is None:
        return False
    return not Path(value).is_absolute() and not PureWindowsPath(value).is_absolute() and not PureWindowsPath(value).drive


def _require_id(value: Any) -> str:
    if not _logical_id(value) or "*" in value:
        _fail("DAILY_TASK_FINISH_PLAN_INVALID")
    return value


def _path_shaped(value: str) -> bool:
    return bool(re.search(r"(?:^|[\s=:])(?:[A-Za-z]:[\\/]|[\\/])", value))


def _validate_plan(store, authority, plan: dict[str, Any]) -> dict[str, Any]:
    top = _exact_object(plan, {
        "protocol_version", "command_id", "instance_expectation", "operator_principal_id",
        "task_id", "root_run_id", "grant_id", "outcome", "classifications",
    })
    if top["protocol_version"] != PROTOCOL_VERSION or top["outcome"] not in _OUTCOMES:
        _fail("DAILY_TASK_FINISH_PLAN_INVALID")
    command_id = _require_id(top["command_id"])
    expected = _exact_object(top["instance_expectation"], {
        "instance_id", "policy_version", "policy_sha256", "journal_identity",
    })
    _require_id(expected["instance_id"])
    _require_id(expected["policy_version"])
    if any(not isinstance(expected[key], str) or _SHA256.fullmatch(expected[key]) is None
           for key in ("policy_sha256", "journal_identity")):
        _fail("DAILY_TASK_FINISH_PLAN_INVALID")
    operator_id = _require_id(top["operator_principal_id"])
    task_id = _require_id(top["task_id"])
    run_id = _require_id(top["root_run_id"])
    grant_id = _require_id(top["grant_id"])
    if len({command_id, operator_id, task_id, run_id, grant_id}) != 5:
        _fail("DAILY_TASK_FINISH_PLAN_INVALID")

    classes_input = _exact_object(top["classifications"], _CLASS_KEYS[top["outcome"]])
    expected_refs = {
        "verifying_event": "evt-" + command_id + ":verifying",
        "terminal_event": "evt-" + command_id + ":terminal",
    }
    assertion_ids: set[str] = set()
    classes: dict[str, dict[str, Any]] = {}
    for key, assertion in classes_input.items():
        if not isinstance(assertion, dict):
            _fail("DAILY_TASK_FINISH_CLASSIFICATION_INVALID")
        try:
            store._validate("nexus.classification_assertion@1.schema.json", assertion)
        except Exception:
            _fail("DAILY_TASK_FINISH_CLASSIFICATION_INVALID")
        assertion_id = assertion.get("assertion_id")
        if (not _logical_id(assertion_id) or assertion.get("subject_type") != "TRACE_EVENT"
                or assertion.get("subject_ref") != expected_refs[key]
                or assertion.get("policy_version") != authority.policy["policy_version"]
                or assertion.get("actor_id") == ""
                or "supersedes" in assertion or _path_shaped(assertion.get("reason", ""))):
            _fail("DAILY_TASK_FINISH_CLASSIFICATION_INVALID")
        if assertion_id in assertion_ids:
            _fail("DAILY_TASK_FINISH_CLASSIFICATION_INVALID")
        assertion_ids.add(assertion_id)
        classes[key] = dict(assertion)

    normalized = {
        "protocol_version": PROTOCOL_VERSION,
        "command_id": command_id,
        "instance_expectation": dict(expected),
        "operator_id": operator_id,
        "task_id": task_id,
        "run_id": run_id,
        "grant_id": grant_id,
        "outcome": top["outcome"],
        "classifications": classes,
        "request_command_id": command_id + ":request",
        "request": {
            "protocol_version": PROTOCOL_VERSION,
            "command_id": command_id,
            "instance_expectation": dict(expected),
            "operator_principal_id": operator_id,
            "task_id": task_id,
            "root_run_id": run_id,
            "grant_id": grant_id,
            "outcome": top["outcome"],
            "classifications": classes,
        },
    }
    normalized["commands"] = _command_closure(command_id, top["outcome"])
    normalized["event_ids"] = ["evt-" + command_id + ":terminal"]
    if top["outcome"] == "SUCCEEDED":
        normalized["event_ids"].insert(0, "evt-" + command_id + ":verifying")
    if (len(normalized["commands"]) != len(set(normalized["commands"]))
            or any(len(item) > 128 for item in normalized["commands"])):
        _fail("DAILY_TASK_FINISH_DUPLICATE_COMMAND_ID")
    return normalized


def _command_closure(command_id: str, outcome: str) -> list[str]:
    commands = [command_id + ":request", command_id + ":class-terminal",
                command_id + ":class-terminal-authorize", command_id + ":terminal",
                command_id + ":terminal-authorize", command_id + ":revoke"]
    if outcome == "SUCCEEDED":
        commands.extend([command_id + ":class-verifying", command_id + ":class-verifying-authorize",
                         command_id + ":verifying", command_id + ":verifying-authorize"])
    return commands


def _replay(store, command_id: str, operation: str, request: dict[str, Any]):
    digest = store._request_hash(operation, request)
    with store._connection() as conn:
        return store._replay_command(conn, command_id, operation, digest)


def _validate_binding(store, expected: dict[str, Any]) -> None:
    actual = store.get_instance_binding_status()
    if (actual.get("state") not in {"FRESH_BOUND_INSTANCE", "LEGACY_ADOPTED_BOUND_INSTANCE"}
            or actual.get("policy_content_binding") != "BOUND"
            or actual.get("instance_id") != expected["instance_id"]
            or actual.get("policy_version") != expected["policy_version"]
            or actual.get("policy_sha256") != expected["policy_sha256"]
            or actual.get("journal_identity") != expected["journal_identity"]):
        _fail("DAILY_TASK_FINISH_INSTANCE_BINDING_MISMATCH")


def _load_projection(store, normalized: dict[str, Any]) -> dict[str, Any]:
    with store._connection() as conn:
        task = conn.execute(
            "SELECT task_id,requester_id,status,root_run_id FROM tasks WHERE task_id=?",
            (normalized["task_id"],),
        ).fetchone()
        run = conn.execute(
            "SELECT run_id,task_id,parent_run_id,executor_kind,status,grant_id,data_boundary_json "
            "FROM runs WHERE run_id=?",
            (normalized["run_id"],),
        ).fetchone()
        grant = conn.execute("SELECT * FROM delegation_grants WHERE grant_id=?", (normalized["grant_id"],)).fetchone()
    if (not task or task["task_id"] != normalized["task_id"]
            or task["root_run_id"] != normalized["run_id"]):
        _fail("DAILY_TASK_FINISH_BINDING_MISMATCH")
    if (not run or run["run_id"] != normalized["run_id"] or run["task_id"] != normalized["task_id"]
            or run["parent_run_id"] is not None or run["executor_kind"] != "ORCHESTRATOR"
            or run["grant_id"] != normalized["grant_id"]):
        _fail("DAILY_TASK_FINISH_BINDING_MISMATCH")
    if not grant:
        _fail("DAILY_TASK_FINISH_AUTHORITY_UNAVAILABLE")
    try:
        boundary = json.loads(run["data_boundary_json"])
    except Exception:
        _fail("DAILY_TASK_FINISH_STATE_INVALID")
    if (not isinstance(boundary, dict)
            or not isinstance(boundary.get("allowed_classifications"), list)
            or not isinstance(boundary.get("handling_tags"), list)
            or any(not isinstance(item, str) for item in boundary["allowed_classifications"] + boundary["handling_tags"])):
        _fail("DAILY_TASK_FINISH_STATE_INVALID")
    return {"task": dict(task), "run": dict(run), "grant": dict(grant), "boundary": boundary}


def _validate_task_and_grant_identity(authority, store, normalized: dict[str, Any], projection: dict[str, Any], *, require_active: bool) -> list[dict[str, Any]]:
    task, run, grant = projection["task"], projection["run"], projection["grant"]
    if task["requester_id"] != normalized["operator_id"]:
        _fail("DAILY_TASK_FINISH_OPERATOR_UNTRUSTED")
    if (grant["parent_grant_id"] is not None or grant["issued_by"] != normalized["operator_id"]
            or grant["task_scope_json"] != json.dumps([normalized["task_id"]], sort_keys=True)):
        _fail("DAILY_TASK_FINISH_GRANT_BINDING_MISMATCH")

    try:
        grant_doc = {
            "schema_id": "nexus.delegation_grant", "schema_version": 1,
            "grant_id": grant["grant_id"],
            "issued_by": grant["issued_by"], "granted_to": grant["granted_to"],
            "task_scope": json.loads(grant["task_scope_json"]),
            "resource_scope": json.loads(grant["resource_scope_json"]),
            "action_scope": json.loads(grant["action_scope_json"]),
            "audience_scope": json.loads(grant["audience_scope_json"]),
            "issued_at": grant["issued_at"], "expires_at": grant["expires_at"],
            "status": grant["status"], "policy_version": grant["policy_version"],
        }
        if grant["credential_ref"] is not None:
            grant_doc["credential_ref"] = grant["credential_ref"]
        store._validate("nexus.delegation_grant@1.schema.json", grant_doc)
    except Exception:
        _fail("DAILY_TASK_FINISH_GRANT_BINDING_MISMATCH")

    with store._connection() as conn:
        issuer = conn.execute("SELECT principal_type,status FROM principals WHERE principal_id=?", (grant["issued_by"],)).fetchone()
        grantee = conn.execute("SELECT principal_type,status FROM principals WHERE principal_id=?", (grant["granted_to"],)).fetchone()
        anchor = conn.execute("SELECT policy_ref FROM trust_anchors WHERE principal_id=?", (normalized["operator_id"],)).fetchone()
    if not issuer or issuer["principal_type"] != "HUMAN":
        _fail("DAILY_TASK_FINISH_OPERATOR_UNTRUSTED")
    if not grantee or grantee["principal_type"] != "SERVICE":
        _fail("DAILY_TASK_FINISH_AUTHORITY_UNAVAILABLE")
    if require_active:
        if issuer["status"] != "ACTIVE" or not anchor or anchor["policy_ref"] != authority.policy["policy_version"] or normalized["operator_id"] not in authority.policy.get("trust_anchors", []):
            _fail("DAILY_TASK_FINISH_OPERATOR_UNTRUSTED")
        if grantee["status"] != "ACTIVE":
            _fail("DAILY_TASK_FINISH_AUTHORITY_UNAVAILABLE")

    scopes = {
        "task_scope": json.loads(grant["task_scope_json"]),
        "resource_scope": json.loads(grant["resource_scope_json"]),
        "action_scope": json.loads(grant["action_scope_json"]),
        "audience_scope": json.loads(grant["audience_scope_json"]),
    }
    from adapters.client.task_start import _ALLOWED_ACTIONS

    expected_refs = ["evt-" + normalized["command_id"] + ":terminal"]
    if normalized["outcome"] == "SUCCEEDED":
        expected_refs.insert(0, "evt-" + normalized["command_id"] + ":verifying")
    needed_actions = {"RUN_TRANSITION", "CLASSIFY"}
    if (scopes["task_scope"] != [normalized["task_id"]]
            or not needed_actions.issubset(scopes["action_scope"])
            or "nexus-runtime" not in scopes["audience_scope"]
            or normalized["run_id"] not in scopes["resource_scope"]
            or not set(expected_refs).issubset(scopes["resource_scope"])
            or any("*" in item or not _logical_id(item) for field in scopes.values() for item in field)
            or any(action not in _ALLOWED_ACTIONS for action in scopes["action_scope"])
            or any(audience not in {"nexus-runtime", "nexus-inspect"} for audience in scopes["audience_scope"])):
        _fail("DAILY_TASK_FINISH_SCOPE_NOT_PREAUTHORIZED")
    for assertion in normalized["classifications"].values():
        if (assertion["actor_id"] != grant["granted_to"]
                or assertion["sensitivity_level"] not in projection["boundary"]["allowed_classifications"]
                or not set(assertion["handling_tags"]).issubset(set(projection["boundary"]["handling_tags"]))):
            _fail("DAILY_TASK_FINISH_CLASSIFICATION_INVALID")
    if require_active and grant["status"] != "ACTIVE":
        _fail("DAILY_TASK_FINISH_AUTHORITY_UNAVAILABLE")
    if not require_active:
        return []
    try:
        chain = authority.validate_delegation_chain(normalized["grant_id"])
    except (InvalidDelegation, AuthorizationDenied):
        _fail("DAILY_TASK_FINISH_AUTHORITY_UNAVAILABLE")
    if (not chain or chain[-1]["grant_id"] != normalized["grant_id"]
            or chain[-1]["parent_grant_id"] is not None
            or chain[-1]["issued_by"] != normalized["operator_id"]
            or chain[-1]["granted_to"] != grant["granted_to"]):
        _fail("DAILY_TASK_FINISH_AUTHORITY_UNAVAILABLE")
    return chain


def _check_mode(store) -> None:
    if RuntimeModeService(store, None).current().get("mode") != "NORMAL":
        _fail("DAILY_TASK_FINISH_RUNTIME_MODE_UNSUPPORTED")
    if ParticipationModeService(store).current().get("mode") != NexusParticipationMode.ACTIVE.value:
        _fail("DAILY_TASK_FINISH_PARTICIPATION_INACTIVE")


def _child_active_count(store, task_id: str) -> int:
    with store._connection() as conn:
        rows = conn.execute("SELECT status FROM runs WHERE task_id=? AND parent_run_id IS NOT NULL", (task_id,)).fetchall()
    return sum(1 for row in rows if row["status"] not in {"SUCCEEDED", "FAILED", "CANCELLED"})


def _unresolved_effect_count(store, task_id: str) -> int:
    try:
        with store._connection() as conn:
            rows = conn.execute(
                "SELECT e.*,r.task_id FROM effects e JOIN runs r ON r.run_id=e.run_id WHERE r.task_id=?",
                (task_id,),
            ).fetchall()
    except Exception:
        _fail("DAILY_TASK_FINISH_EFFECT_GATE_UNRESOLVED")
    unresolved = 0
    for row in rows:
        try:
            effect = json.loads(row["effect_json"])
            store._validate("nexus.effect@1.schema.json", effect)
            pairs = {
                "effect_id": "effect_id", "run_id": "run_id", "tool_id": "tool_id",
                "action_type": "action_type", "target_ref": "target_ref",
                "payload_integrity_hash": "payload_integrity_hash", "idempotency_key": "idempotency_key",
                "grant_id": "grant_id", "execution_state": "execution_state",
                "effect_outcome": "effect_outcome", "reconciliation_status": "reconciliation_status",
            }
            if any(effect.get(key) != row[column] for key, column in pairs.items()):
                raise ValueError("effect projection mismatch")
            if effect.get("approval_ref") != row["approval_ref"] or effect.get("external_receipt_ref") != row["external_receipt_ref"]:
                raise ValueError("effect optional projection mismatch")
            closed = (effect["execution_state"] in {"FINISHED", "CANCELLED"}
                      and effect["effect_outcome"] in {"COMMITTED", "NOT_COMMITTED"}
                      and effect["reconciliation_status"] in {"NOT_REQUIRED", "RESOLVED"})
            if not closed:
                unresolved += 1
        except Exception:
            unresolved += 1
    return unresolved


def _check_work_closure(store, task_id: str) -> tuple[int, int]:
    active_children = _child_active_count(store, task_id)
    if active_children:
        _fail("DAILY_TASK_CHILD_WORK_ACTIVE")
    unresolved_effects = _unresolved_effect_count(store, task_id)
    if unresolved_effects:
        _fail("DAILY_TASK_EFFECTS_UNRESOLVED")
    return active_children, unresolved_effects


def _assert_command_namespace(store, normalized: dict[str, Any], *, request_bound: bool) -> None:
    commands = normalized["commands"]
    expected: dict[str, tuple[str, str]] = {
        normalized["request_command_id"]: (
            "daily_task_finish_request", store._request_hash("daily_task_finish_request", normalized["request"]),
        ),
    }
    classes = normalized["classifications"]
    grant_id = normalized["grant_id"]
    for key, stage in (("verifying_event", "verifying"), ("terminal_event", "terminal")):
        assertion = classes.get(key)
        if assertion is None:
            continue
        class_id = normalized["command_id"] + ":class-" + stage
        class_request = {"assertion": assertion, "grant_id": grant_id}
        expected[class_id] = ("record_classification_assertion", store._request_hash("record_classification_assertion", class_request))
    terminal = classes["terminal_event"]
    expected_state = "VERIFYING" if normalized["outcome"] == "SUCCEEDED" else "RUNNING"
    transition_specs = []
    if normalized["outcome"] == "SUCCEEDED":
        transition_specs.append(("verifying", "RUNNING", "VERIFYING", classes["verifying_event"]))
    transition_specs.append(("terminal", expected_state, _terminal_status(normalized), terminal))
    for stage, before, after, assertion in transition_specs:
        command_id = normalized["command_id"] + ":" + stage
        request = _transition_request(normalized, before, after, assertion)
        expected[command_id] = ("transition_run", store._request_hash("transition_run", request))
    revoke_id = normalized["command_id"] + ":revoke"
    revoke_request = {"grant_id": grant_id, "expected": "ACTIVE", "target": "REVOKED"}
    expected[revoke_id] = ("transition_grant", store._request_hash("transition_grant", revoke_request))
    event_to_command = {event_ref: event_ref[4:] for event_ref in normalized["event_ids"]}
    assertion_to_command = {
        assertion["assertion_id"]: normalized["command_id"] + ":class-" + stage
        for key, stage in (("verifying_event", "verifying"), ("terminal_event", "terminal"))
        if (assertion := classes.get(key)) is not None
    }
    class_ref_to_command = {
        assertion["subject_ref"]: normalized["command_id"] + ":class-" + stage
        for key, stage in (("verifying_event", "verifying"), ("terminal_event", "terminal"))
        if (assertion := classes.get(key)) is not None
    }
    with store._connection() as conn:
        ledger = {row["command_id"]: (row["operation"], row["request_hash"]) for row in conn.execute(
            "SELECT command_id,operation,request_hash FROM command_ledger WHERE command_id IN (%s)" % ",".join("?" for _ in commands), commands
        ).fetchall()}
        authority_ids = {row[0] for row in conn.execute(
            "SELECT command_id FROM authority_events WHERE command_id IN (%s)" % ",".join("?" for _ in commands), commands
        ).fetchall()}
        event_ids = {row[0] for row in conn.execute(
            "SELECT event_id FROM trace_events WHERE event_id IN (%s)" % ",".join("?" for _ in normalized["event_ids"]), normalized["event_ids"]
        ).fetchall()}
        classified = {row[0] for row in conn.execute(
            "SELECT subject_ref FROM classification_assertions WHERE subject_type='TRACE_EVENT' AND subject_ref IN (%s)" % ",".join("?" for _ in normalized["event_ids"]), normalized["event_ids"]
        ).fetchall()}
        assertion_rows = {row["assertion_id"]: dict(row) for row in conn.execute(
            "SELECT assertion_id,subject_type,subject_ref,sensitivity_level,handling_tags_json,policy_version,reason,actor_id,supersedes "
            "FROM classification_assertions WHERE assertion_id IN (%s)" % ",".join("?" for _ in assertion_to_command),
            list(assertion_to_command),
        ).fetchall()}
    if (not request_bound and ledger) or any(
        command_id not in expected or expected[command_id] != identity for command_id, identity in ledger.items()
    ):
        _fail("DAILY_TASK_FINISH_COMMAND_ID_CONFLICT")
    if not request_bound and (authority_ids or event_ids or classified):
        _fail("DAILY_TASK_FINISH_COMMAND_ID_CONFLICT")
    for event_id in event_ids:
        transition_command = event_to_command[event_id]
        if transition_command not in ledger:
            _fail("DAILY_TASK_FINISH_COMMAND_ID_CONFLICT" if not request_bound else "DAILY_TASK_FINISH_STATE_CONFLICT")
        matching_ref = event_id
        class_command = class_ref_to_command.get(matching_ref)
        if class_command is None or class_command not in ledger:
            _fail("DAILY_TASK_FINISH_STATE_CONFLICT")
    for ref in classified:
        command_id = class_ref_to_command.get(ref)
        if command_id is None or command_id not in ledger:
            _fail("DAILY_TASK_FINISH_COMMAND_ID_CONFLICT" if not request_bound else "DAILY_TASK_FINISH_STATE_CONFLICT")
    for assertion_id, command_id in assertion_to_command.items():
        if (command_id in ledger) != (assertion_id in assertion_rows):
            _fail("DAILY_TASK_FINISH_STATE_CONFLICT")
    for assertion_id, row in assertion_rows.items():
        command_id = assertion_to_command[assertion_id]
        key = "verifying_event" if "class-verifying" in command_id else "terminal_event"
        assertion = classes[key]
        expected_row = {
            "subject_type": assertion["subject_type"], "subject_ref": assertion["subject_ref"],
            "sensitivity_level": assertion["sensitivity_level"],
            "handling_tags_json": json.dumps(assertion["handling_tags"], sort_keys=True),
            "policy_version": assertion["policy_version"], "reason": assertion["reason"],
            "actor_id": assertion["actor_id"], "supersedes": None,
        }
        if (not request_bound or command_id not in ledger or any(row[field] != value for field, value in expected_row.items())):
            _fail("DAILY_TASK_FINISH_COMMAND_ID_CONFLICT" if not request_bound else "DAILY_TASK_FINISH_STATE_CONFLICT")


def _transition_request(normalized: dict[str, Any], expected: str, next_state: str, assertion: dict[str, Any]) -> dict[str, Any]:
    return {"run_id": normalized["run_id"], "expected_state": expected, "next_state": next_state,
            "classification_assertion_ref": assertion["assertion_id"]}


def _transition_committed(store, normalized: dict[str, Any], stage: str, expected: str, next_state: str, assertion: dict[str, Any]) -> dict[str, Any] | None:
    command_id = normalized["command_id"] + ":" + stage
    result = _replay(store, command_id, "transition_run", _transition_request(normalized, expected, next_state, assertion))
    if result is not None and (result.get("run_id") != normalized["run_id"]
                               or result.get("status") != next_state
                               or result.get("event_id") != "evt-" + command_id):
        _fail("DAILY_TASK_FINISH_STATE_CONFLICT")
    return result


def _projection_matches_terminal(store, normalized: dict[str, Any], projection: dict[str, Any]) -> None:
    outcome = normalized["outcome"]
    if (projection["run"]["status"] != _TERMINAL_RUN[outcome]
            or projection["task"]["status"] != _TERMINAL_TASK[outcome]):
        _fail("DAILY_TASK_FINISH_STATE_CONFLICT")


def _result(store, normalized: dict[str, Any]) -> dict[str, Any]:
    projection = _load_projection(store, normalized)
    _projection_matches_terminal(store, normalized, projection)
    if projection["grant"]["status"] != "REVOKED":
        _fail("DAILY_TASK_FINISH_STATE_CONFLICT")
    return {
        "status": "DAILY_TASK_FINISHED", "task_id": normalized["task_id"],
        "root_run_id": normalized["run_id"], "outcome": normalized["outcome"],
        "task_status": projection["task"]["status"], "run_status": projection["run"]["status"],
        "grant_id": normalized["grant_id"], "grant_status": "REVOKED",
    }


def _ensure_exact_request(store, normalized: dict[str, Any]) -> bool:
    result = _replay(store, normalized["request_command_id"], "daily_task_finish_request", normalized["request"])
    if result is not None and result != {"status": "REQUEST_BOUND"}:
        _fail("DAILY_TASK_FINISH_REQUEST_INVALID")
    return result is not None


def _terminal_status(normalized: dict[str, Any]) -> str:
    return _TERMINAL_RUN[normalized["outcome"]]


def _confirm_summary(normalized: dict[str, Any], projection: dict[str, Any], child_count: int, effect_count: int) -> dict[str, Any]:
    return {
        "task_id": normalized["task_id"], "root_run_id": normalized["run_id"],
        "outcome": normalized["outcome"], "grant_id": normalized["grant_id"],
        "grant_expires_at": projection["grant"]["expires_at"],
        "current_run_status": projection["run"]["status"],
        "current_task_status": projection["task"]["status"],
        "child_active_count": child_count, "unresolved_effect_count": effect_count,
    }


def finish_daily_task(*, store, authority, trace, plan: dict[str, Any],
                      confirmation: Callable[[str, dict[str, Any]], bool]) -> dict[str, Any]:
    """Finish one existing Root, recover exact partial work, then revoke its Grant."""
    try:
        if (authority.store is not store or trace.store is not store or trace.authority is not authority
                or authority.policy != store.policy):
            _fail("DAILY_TASK_FINISH_COMPOSITION_INVALID")
        normalized = _validate_plan(store, authority, plan)
        request_bound = _ensure_exact_request(store, normalized)
        _validate_binding(store, normalized["instance_expectation"])
        _assert_command_namespace(store, normalized, request_bound=request_bound)
        projection = _load_projection(store, normalized)
        _validate_task_and_grant_identity(authority, store, normalized, projection, require_active=False)

        verifying = normalized["classifications"].get("verifying_event")
        terminal = normalized["classifications"]["terminal_event"]
        verifying_transition = None
        if normalized["outcome"] == "SUCCEEDED":
            verifying_transition = _transition_committed(store, normalized, "verifying", "RUNNING", "VERIFYING", verifying)
        terminal_transition = _transition_committed(
            store, normalized, "terminal",
            "VERIFYING" if normalized["outcome"] == "SUCCEEDED" else "RUNNING",
            _terminal_status(normalized), terminal,
        )

        if projection["run"]["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            if not request_bound:
                _fail("DAILY_TASK_FINISH_STATE_INVALID")
            if (terminal_transition is None or projection["run"]["status"] != _terminal_status(normalized)
                    or (normalized["outcome"] == "SUCCEEDED" and verifying_transition is None)):
                _fail("DAILY_TASK_FINISH_STATE_CONFLICT")
            _projection_matches_terminal(store, normalized, projection)
            revoke_request = {"grant_id": normalized["grant_id"], "expected": "ACTIVE", "target": "REVOKED"}
            revoke = _replay(store, normalized["command_id"] + ":revoke", "transition_grant", revoke_request)
            if projection["grant"]["status"] == "REVOKED":
                if revoke is None or revoke != {"grant_id": normalized["grant_id"], "status": "REVOKED"}:
                    _fail("DAILY_TASK_FINISH_STATE_CONFLICT")
                return _result(store, normalized)
            if projection["grant"]["status"] != "ACTIVE":
                _fail("DAILY_TASK_FINISH_AUTHORITY_UNAVAILABLE")
            if revoke is not None:
                _fail("DAILY_TASK_FINISH_STATE_CONFLICT")
            authority.revoke_grant(normalized["grant_id"], normalized["command_id"] + ":revoke")
            return _result(store, normalized)

        if projection["run"]["status"] not in ({"RUNNING", "VERIFYING"} if normalized["outcome"] == "SUCCEEDED" else {"RUNNING"}):
            _fail("DAILY_TASK_FINISH_STATE_CONFLICT" if request_bound else "DAILY_TASK_FINISH_STATE_INVALID")
        if projection["task"]["status"] != "ACTIVE":
            _fail("DAILY_TASK_FINISH_STATE_CONFLICT" if request_bound else "DAILY_TASK_FINISH_STATE_INVALID")
        if projection["run"]["status"] == "VERIFYING" and verifying_transition is None:
            _fail("DAILY_TASK_FINISH_STATE_CONFLICT")
        if projection["run"]["status"] == "RUNNING" and verifying_transition is not None:
            _fail("DAILY_TASK_FINISH_STATE_CONFLICT")
        if terminal_transition is not None:
            _fail("DAILY_TASK_FINISH_STATE_CONFLICT")
        if projection["grant"]["status"] != "ACTIVE":
            _fail("DAILY_TASK_FINISH_AUTHORITY_UNAVAILABLE")

        _check_mode(store)
        _check_work_closure(store, normalized["task_id"])
        _validate_task_and_grant_identity(authority, store, normalized, projection, require_active=True)
        # A new request must start from the exact Active/Running state. A
        # durable request may resume only the state sequence it owns.
        if not request_bound and (projection["task"]["status"] != "ACTIVE" or projection["run"]["status"] != "RUNNING"):
            _fail("DAILY_TASK_FINISH_STATE_INVALID")
        if not request_bound:
            if not confirmation("FINISH " + normalized["task_id"] + " " + normalized["outcome"],
                                _confirm_summary(normalized, projection, 0, 0)):
                _fail("DAILY_TASK_FINISH_CONFIRMATION_DENIED")
            # Recheck all gates immediately before the first canonical write.
            _validate_binding(store, normalized["instance_expectation"])
            projection = _load_projection(store, normalized)
            _validate_task_and_grant_identity(authority, store, normalized, projection, require_active=True)
            _check_mode(store)
            _check_work_closure(store, normalized["task_id"])
            if projection["task"]["status"] != "ACTIVE" or projection["run"]["status"] != "RUNNING":
                _fail("DAILY_TASK_FINISH_STATE_INVALID")
            store.bind_command_request(command_id=normalized["request_command_id"],
                                       operation="daily_task_finish_request", request=normalized["request"])
            request_bound = True

        def ensure_authority_for_forward_work():
            current = _load_projection(store, normalized)
            _validate_task_and_grant_identity(authority, store, normalized, current, require_active=True)
            _check_mode(store)
            return current

        if normalized["outcome"] == "SUCCEEDED":
            verifying_class = normalized["classifications"]["verifying_event"]
            if _replay(store, normalized["command_id"] + ":class-verifying", "record_classification_assertion",
                       {"assertion": verifying_class, "grant_id": normalized["grant_id"]}) is None:
                ensure_authority_for_forward_work()
                _check_work_closure(store, normalized["task_id"])
            authority.record_classification_assertion(
                verifying_class, grant_id=normalized["grant_id"], task_id=normalized["task_id"],
                audience="nexus-runtime", command_id=normalized["command_id"] + ":class-verifying",
            )
            existing = _transition_committed(store, normalized, "verifying", "RUNNING", "VERIFYING", verifying_class)
            if existing is None:
                ensure_authority_for_forward_work()
                _check_work_closure(store, normalized["task_id"])
                trace.transition_run(command_id=normalized["command_id"] + ":verifying", run_id=normalized["run_id"],
                                     expected_state="RUNNING", next_state="VERIFYING",
                                     classification_assertion_ref=verifying_class["assertion_id"])
            else:
                _assert_run_status(store, normalized["run_id"], "VERIFYING")

        if _replay(store, normalized["command_id"] + ":class-terminal", "record_classification_assertion",
                   {"assertion": terminal, "grant_id": normalized["grant_id"]}) is None:
            ensure_authority_for_forward_work()
            _check_work_closure(store, normalized["task_id"])
        authority.record_classification_assertion(
            terminal, grant_id=normalized["grant_id"], task_id=normalized["task_id"],
            audience="nexus-runtime", command_id=normalized["command_id"] + ":class-terminal",
        )
        expected_state = "VERIFYING" if normalized["outcome"] == "SUCCEEDED" else "RUNNING"
        existing = _transition_committed(store, normalized, "terminal", expected_state,
                                         _terminal_status(normalized), terminal)
        if existing is None:
            ensure_authority_for_forward_work()
            _check_work_closure(store, normalized["task_id"])
            trace.transition_run(command_id=normalized["command_id"] + ":terminal", run_id=normalized["run_id"],
                                 expected_state=expected_state, next_state=_terminal_status(normalized),
                                 classification_assertion_ref=terminal["assertion_id"])
        else:
            _assert_run_status(store, normalized["run_id"], _terminal_status(normalized))

        # Terminal transition is the authority-bearing close. Revoke is last and
        # intentionally does not require the Grant to remain unexpired.
        authority.revoke_grant(normalized["grant_id"], normalized["command_id"] + ":revoke")
        return _result(store, normalized)
    except DailyTaskFinishError:
        raise
    except CommandConflict:
        raise DailyTaskFinishError("COMMAND_CONFLICT") from None
    except (InvalidDelegation, AuthorizationDenied, RuntimeDenied):
        raise DailyTaskFinishError("DAILY_TASK_FINISH_AUTHORITY_UNAVAILABLE") from None
    except (InvalidRunTransition, TraceAdmissionDenied):
        raise DailyTaskFinishError("DAILY_TASK_FINISH_STATE_CONFLICT") from None
    except Exception:
        raise DailyTaskFinishError("DAILY_TASK_FINISH_FAILED") from None


def _assert_run_status(store, run_id: str, status: str) -> None:
    with store._connection() as conn:
        row = conn.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if not row or row["status"] != status:
        _fail("DAILY_TASK_FINISH_STATE_CONFLICT")
