"""Explicit HUMAN-authorized start of one bounded daily Task.

This is a narrow operator composition over the existing Authority and Hosted
Bridge APIs. It does not execute the Task or create any continuation state.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Callable

from adapters.client.hosted import CodexHostedBridge
from kernel.authority.errors import InvalidDelegation
from kernel.object.errors import CommandConflict
from kernel.participation import NexusParticipationMode, ParticipationModeService
from kernel.runtime.errors import RuntimeDenied
from kernel.runtime.modes import RuntimeModeService


PROTOCOL_VERSION = "nexus.daily_task_start@1"
_LOGICAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")

# Closed to the established task-work action vocabulary. Root creation always
# needs the first five. The remaining actions have existing consumers in Core.
_ALLOWED_ACTIONS = frozenset({
    "RUN_CREATE", "RUN_TRANSITION", "TRACE_APPEND", "OBJECT_WRITE", "CLASSIFY",
    "INSPECT", "VERIFY", "MEMORY_ADMIT", "MEMORY_SEARCH", "TOOL_READ",
})
_ROOT_ACTIONS = frozenset({
    "RUN_CREATE", "RUN_TRANSITION", "TRACE_APPEND", "OBJECT_WRITE", "CLASSIFY",
})
_ALLOWED_AUDIENCES = frozenset({"nexus-runtime", "nexus-inspect"})

# These exact suffixes mirror CodexHostedBridge.create_task_root and its
# current Core calls. This is deliberately local to this application, not a
# general command registry.
_ROOT_CHILD_SUFFIXES = (
    "request", "task", "budget-account", "class-root-run", "class-root-created", "root-create",
    "class-input", "authorize-input", "put-input", "class-input-event", "trace-input", "class-contract",
    "bind-contract", "create-dag", "class-root-manifest", "bind-root-manifest", "class-root-ready",
    "root-ready", "class-root-running", "root-running",
)
_ROOT_NESTED_COMMAND_SUFFIXES = (
    "root-create-authorize", "trace-input-authorize",
    "class-root-run-authorize", "class-root-created-authorize", "class-input-authorize",
    "class-input-event-authorize", "class-contract-authorize", "bind-contract-authorize",
    "bind-contract-object", "bind-contract-logical-ref", "create-dag-authorize",
    "class-root-manifest-authorize", "bind-root-manifest-authorize", "bind-root-manifest-object",
    "class-root-ready-authorize", "root-ready-authorize", "class-root-running-authorize",
    "root-running-authorize",
)
_CLASS_KEYS = (
    "root_run", "root_created_event", "input_object", "input_event", "task_contract",
    "root_manifest", "root_ready_event", "root_running_event",
)


class DailyTaskStartError(Exception):
    """Stable, sanitized operator-facing reason code."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


def _fail(reason: str) -> None:
    raise DailyTaskStartError(reason)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _pairs_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DAILY_TASK_PLAN_INVALID")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("DAILY_TASK_PLAN_INVALID")


def read_task_start_plan(path: str | Path) -> dict[str, Any]:
    """Read a process-local plan as strict UTF-8 JSON without persisting its path."""
    try:
        raw = Path(path).expanduser().read_bytes()
        value = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_pairs_no_duplicates,
                           parse_constant=_reject_constant)
    except Exception:
        raise DailyTaskStartError("DAILY_TASK_PLAN_INVALID") from None
    if not isinstance(value, dict):
        _fail("DAILY_TASK_PLAN_INVALID")
    return value


def _exact_object(value: Any, required: set[str], optional: set[str] = frozenset()) -> dict[str, Any]:
    if not isinstance(value, dict) or not required.issubset(value) or not set(value).issubset(required | optional):
        _fail("DAILY_TASK_PLAN_INVALID")
    return value


def _logical_id(value: Any) -> bool:
    if not isinstance(value, str) or value in {".", ".."} or _LOGICAL_ID.fullmatch(value) is None:
        return False
    return not Path(value).is_absolute() and not PureWindowsPath(value).is_absolute() and not PureWindowsPath(value).drive


def _require_id(value: Any) -> str:
    if not _logical_id(value):
        _fail("DAILY_TASK_PLAN_INVALID")
    return value


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or _TIMESTAMP.fullmatch(value) is None:
        _fail("DAILY_TASK_PLAN_INVALID")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except ValueError:
        _fail("DAILY_TASK_PLAN_INVALID")
    if parsed.isoformat(timespec="microseconds").replace("+00:00", "Z") != value:
        _fail("DAILY_TASK_PLAN_INVALID")
    return parsed


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_plan(store, authority, plan: dict[str, Any]) -> dict[str, Any]:
    top = _exact_object(plan, {
        "protocol_version", "command_id", "instance_expectation", "operator_principal_id",
        "runtime_principal_id", "grant", "root",
    })
    if top["protocol_version"] != PROTOCOL_VERSION:
        _fail("DAILY_TASK_PLAN_INVALID")
    command_id = _require_id(top["command_id"])
    expected = _exact_object(top["instance_expectation"], {
        "instance_id", "policy_version", "policy_sha256", "journal_identity",
    })
    for key in ("instance_id", "policy_version"):
        _require_id(expected[key])
    if not isinstance(expected["policy_sha256"], str) or _SHA256.fullmatch(expected["policy_sha256"]) is None:
        _fail("DAILY_TASK_PLAN_INVALID")
    if not isinstance(expected["journal_identity"], str) or _SHA256.fullmatch(expected["journal_identity"]) is None:
        _fail("DAILY_TASK_PLAN_INVALID")
    operator_id = _require_id(top["operator_principal_id"])
    runtime_id = _require_id(top["runtime_principal_id"])
    if operator_id == runtime_id:
        _fail("DAILY_TASK_PLAN_INVALID")

    grant_input = _exact_object(top["grant"], {
        "grant_id", "issued_at", "expires_at", "action_scope", "audience_scope",
    }, {"additional_resource_scope"})
    grant_id = _require_id(grant_input["grant_id"])
    issued = _timestamp(grant_input["issued_at"])
    expires = _timestamp(grant_input["expires_at"])
    if issued >= expires:
        _fail("DAILY_TASK_GRANT_TIME_INVALID")
    actions = grant_input["action_scope"]
    audiences = grant_input["audience_scope"]
    additional = grant_input.get("additional_resource_scope", [])
    if not isinstance(actions, list) or not actions or any(not isinstance(item, str) for item in actions):
        _fail("DAILY_TASK_ACTION_SCOPE_INVALID")
    if len(actions) != len(set(actions)) or not _ROOT_ACTIONS.issubset(actions):
        _fail("DAILY_TASK_ACTION_SCOPE_INVALID")
    if any(item not in _ALLOWED_ACTIONS for item in actions):
        _fail("DAILY_TASK_FORBIDDEN_ACTION")
    if not isinstance(audiences, list) or not audiences or any(not isinstance(item, str) for item in audiences):
        _fail("DAILY_TASK_AUDIENCE_SCOPE_INVALID")
    if len(audiences) != len(set(audiences)) or "nexus-runtime" not in audiences or any(item not in _ALLOWED_AUDIENCES for item in audiences):
        _fail("DAILY_TASK_AUDIENCE_SCOPE_INVALID")
    if not isinstance(additional, list) or any(not _logical_id(item) for item in additional):
        _fail("DAILY_TASK_RESOURCE_SCOPE_INVALID")
    if len(additional) != len(set(additional)):
        _fail("DAILY_TASK_RESOURCE_SCOPE_INVALID")

    root_input = _exact_object(top["root"], {
        "created_at", "task_id", "requester_id", "root_run_id", "budget_account_id", "budget_limits",
        "input_object_id", "input_payload", "task_contract", "contract_object_id", "dag_nodes",
        "root_manifest_object_id", "data_boundary", "classifications",
    })
    root_created = _timestamp(root_input["created_at"])
    declared_ids = [
        _require_id(root_input[key]) for key in (
            "task_id", "requester_id", "root_run_id", "budget_account_id", "input_object_id",
            "contract_object_id", "root_manifest_object_id",
        )
    ] + [grant_id]
    if len(declared_ids) != len(set(declared_ids)):
        _fail("DAILY_TASK_PLAN_INVALID")
    if root_input["requester_id"] != operator_id:
        _fail("DAILY_TASK_REQUESTER_MISMATCH")
    if not isinstance(root_input["input_payload"], str):
        _fail("DAILY_TASK_PLAN_INVALID")
    try:
        input_bytes = root_input["input_payload"].encode("utf-8", errors="strict")
    except UnicodeError:
        _fail("DAILY_TASK_PLAN_INVALID")
    if not input_bytes:
        _fail("DAILY_TASK_PLAN_INVALID")
    if root_input["dag_nodes"] != []:
        _fail("DAILY_TASK_DAG_UNSUPPORTED")
    limits = _exact_object(root_input["budget_limits"], {
        "amount_limit", "unit", "model_call_limit", "tool_call_limit", "child_run_limit",
    })
    if any(type(limits[key]) is not int or limits[key] < 0 for key in (
        "amount_limit", "model_call_limit", "tool_call_limit", "child_run_limit",
    )) or not isinstance(limits["unit"], str) or not limits["unit"].strip():
        _fail("DAILY_TASK_BUDGET_INVALID")
    if not isinstance(root_input["task_contract"], dict):
        _fail("DAILY_TASK_TASK_CONTRACT_INVALID")
    try:
        contract_created = _timestamp(root_input["task_contract"].get("created_at"))
    except DailyTaskStartError:
        _fail("DAILY_TASK_CREATED_AT_MISMATCH")
    if contract_created != root_created:
        _fail("DAILY_TASK_CREATED_AT_MISMATCH")
    if not issued <= root_created < expires:
        _fail("DAILY_TASK_CREATED_AT_INVALID")
    boundary = root_input["data_boundary"]
    if not isinstance(boundary, dict):
        _fail("DAILY_TASK_PLAN_INVALID")
    try:
        store._validate("nexus.run@1.schema.json", {
            "schema_id": "nexus.run", "schema_version": 1, "run_id": root_input["root_run_id"],
            "task_id": root_input["task_id"], "executor_kind": "ORCHESTRATOR", "status": "CREATED",
            "grant_id": grant_id, "data_boundary": boundary,
            "classification_assertion_ref": "classification-placeholder", "created_at": root_input["created_at"],
        })
        effective_contract = {
            **root_input["task_contract"], "task_id": root_input["task_id"],
            "requester_id": operator_id, "budget_account_ref": root_input["budget_account_id"],
            "input_object_refs": [root_input["input_object_id"]],
        }
        store._validate("nexus.task_contract@1.schema.json", effective_contract)
        store._validate("nexus.budget_account@1.schema.json", {
            "schema_id": "nexus.budget_account", "schema_version": 1,
            "account_id": root_input["budget_account_id"], "task_id": root_input["task_id"],
            "limit": limits["amount_limit"], "reserved": 0, "consumed": 0, "unit": limits["unit"],
            "model_call_limit": limits["model_call_limit"], "tool_call_limit": limits["tool_call_limit"],
            "child_run_limit": limits["child_run_limit"],
        })
    except Exception:
        _fail("DAILY_TASK_ROOT_SCHEMA_INVALID")

    root_command = command_id + ":root"
    expected_subjects = {
        "root_run": ("RUN", root_input["root_run_id"]),
        "root_created_event": ("TRACE_EVENT", "evt-" + root_command + "-root-create"),
        "input_object": ("OBJECT", root_input["input_object_id"]),
        "input_event": ("TRACE_EVENT", "evt-" + root_command + "-trace-input"),
        "task_contract": ("OBJECT", root_input["contract_object_id"]),
        "root_manifest": ("OBJECT", root_input["root_manifest_object_id"]),
        "root_ready_event": ("TRACE_EVENT", "evt-" + root_command + "-root-ready"),
        "root_running_event": ("TRACE_EVENT", "evt-" + root_command + "-root-running"),
    }
    classifications = root_input["classifications"]
    if not isinstance(classifications, dict) or set(classifications) != set(_CLASS_KEYS):
        _fail("DAILY_TASK_CLASSIFICATION_INVALID")
    assertion_ids = []
    for key, (subject_type, subject_ref) in expected_subjects.items():
        assertion = classifications[key]
        if not isinstance(assertion, dict):
            _fail("DAILY_TASK_CLASSIFICATION_INVALID")
        try:
            store._validate("nexus.classification_assertion@1.schema.json", assertion)
        except Exception:
            _fail("DAILY_TASK_CLASSIFICATION_INVALID")
        if (assertion.get("subject_type") != subject_type or assertion.get("subject_ref") != subject_ref
                or assertion.get("policy_version") != authority.policy["policy_version"]
                or assertion.get("actor_id") != runtime_id
                or assertion.get("sensitivity_level") not in boundary.get("allowed_classifications", [])
                or not set(assertion.get("handling_tags", [])).issubset(set(boundary.get("handling_tags", [])))
                or "supersedes" in assertion):
            _fail("DAILY_TASK_CLASSIFICATION_INVALID")
        assertion_ids.append(_require_id(assertion["assertion_id"]))
    if len(assertion_ids) != len(set(assertion_ids)):
        _fail("DAILY_TASK_CLASSIFICATION_INVALID")

    grant = {
        "schema_id": "nexus.delegation_grant", "schema_version": 1,
        "grant_id": grant_id, "issued_by": operator_id, "granted_to": runtime_id,
        "task_scope": [root_input["task_id"]],
        "resource_scope": [],
        "action_scope": sorted(actions), "audience_scope": sorted(audiences),
        "issued_at": grant_input["issued_at"], "expires_at": grant_input["expires_at"],
        "status": "ACTIVE", "policy_version": authority.policy["policy_version"],
    }
    resources = {
        "task:" + root_input["task_id"], root_input["root_run_id"], root_input["input_object_id"],
        root_input["contract_object_id"], root_input["root_manifest_object_id"],
    }
    for assertion in classifications.values():
        resources.add(assertion["subject_ref"])
    resources.update(additional)
    if any(not _logical_id(resource) for resource in resources) or any("*" in resource for resource in resources):
        _fail("DAILY_TASK_RESOURCE_SCOPE_INVALID")
    grant["resource_scope"] = sorted(resources)
    try:
        store._validate("nexus.delegation_grant@1.schema.json", grant)
        authority._validate_grant_shape(grant)
    except Exception:
        _fail("DAILY_TASK_GRANT_INVALID")

    task_record = {
        "schema_id": "nexus.task", "schema_version": 1, "task_id": root_input["task_id"],
        "requester_id": operator_id, "status": "CREATED", "created_at": root_input["created_at"],
        "command_id": root_command + "-task",
    }
    root_run = {
        "schema_id": "nexus.run", "schema_version": 1, "run_id": root_input["root_run_id"],
        "task_id": root_input["task_id"], "executor_kind": "ORCHESTRATOR", "status": "CREATED",
        "grant_id": grant_id, "data_boundary": boundary,
        "classification_assertion_ref": classifications["root_run"]["assertion_id"],
        "created_at": root_input["created_at"],
    }
    root_manifest = {
        "schema_id": "nexus.run_manifest", "schema_version": 1, "executor_kind": "ORCHESTRATOR",
        "runtime_version": "0.1", "policy_version": authority.policy["policy_version"],
        "schema_versions": {"nexus.run_manifest": 1, "nexus.task_contract": 1},
        "input_object_refs": [root_input["input_object_id"]], "authority_grant_ref": grant_id,
        "data_boundary": boundary, "classification_assertion_ref": classifications["root_run"]["assertion_id"],
        "task_contract_ref": root_input["contract_object_id"], "dag_version": "1", "scheduler_version": "1",
    }
    try:
        store._validate("nexus.task@1.schema.json", task_record)
        store._validate("nexus.run@1.schema.json", root_run)
        store._validate("nexus.run_manifest@1.schema.json", root_manifest)
    except Exception:
        _fail("DAILY_TASK_ROOT_SCHEMA_INVALID")

    command_ids = _command_id_closure(command_id)
    if (len(command_ids) != len(set(command_ids))
            or any(not isinstance(item, str) or len(item) > 128 for item in command_ids)):
        _fail("DAILY_TASK_DUPLICATE_COMMAND_ID")
    if len(set(declared_ids + assertion_ids)) != len(declared_ids + assertion_ids):
        _fail("DAILY_TASK_PLAN_INVALID")

    root = dict(root_input)
    root["task_contract"] = effective_contract
    root["command_id"] = root_command
    request = {
        "protocol_version": PROTOCOL_VERSION,
        "command_id": command_id,
        "instance_expectation": dict(expected),
        "operator_principal_id": operator_id,
        "runtime_principal_id": runtime_id,
        "grant": grant,
        "root": {
            **root,
            "task_contract_template": root_input["task_contract"],
            "input_payload": {"sha256": _sha256(input_bytes), "byte_size": len(input_bytes)},
            "root_manifest": root_manifest,
        },
    }
    return {
        "command_id": command_id, "request_command_id": command_id + ":request",
        "grant_command_id": command_id + ":grant", "root_command_id": root_command,
        "instance_expectation": dict(expected), "operator_id": operator_id, "runtime_id": runtime_id,
        "issued": issued, "expires": expires, "created": root_created, "grant": grant, "root": root_input,
        "effective_contract": effective_contract, "root_manifest": root_manifest,
        "input_bytes": input_bytes, "request": request, "command_ids": command_ids,
    }


def _command_id_closure(command_id: str) -> list[str]:
    root_command = command_id + ":root"
    return [command_id + ":request", command_id + ":grant", root_command,
            *(root_command + "-" + suffix for suffix in _ROOT_CHILD_SUFFIXES),
            *(root_command + "-" + suffix for suffix in _ROOT_NESTED_COMMAND_SUFFIXES)]


def _bridge_request(store, normalized: dict[str, Any]) -> dict[str, Any]:
    root = normalized["root"]
    classifications = root["classifications"]
    return {
        "bridge_version": CodexHostedBridge.VERSION,
        "created_at": root["created_at"], "task_id": root["task_id"],
        "requester_id": normalized["operator_id"], "grant_id": normalized["grant"]["grant_id"],
        "root_run_id": root["root_run_id"], "budget_account_id": root["budget_account_id"],
        "budget_limits": root["budget_limits"], "input_object_id": root["input_object_id"],
        "input_payload_sha256": _sha256(normalized["input_bytes"]),
        "task_contract": normalized["effective_contract"], "contract_object_id": root["contract_object_id"],
        "dag_nodes": [], "root_manifest_object_id": root["root_manifest_object_id"],
        "root_manifest": normalized["root_manifest"], "data_boundary": root["data_boundary"],
        "classifications": classifications,
    }


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
        _fail("DAILY_TASK_INSTANCE_BINDING_MISMATCH")


def _validate_current_authority(
    store, authority, normalized: dict[str, Any], *, require_current_created_at: bool,
) -> None:
    root = normalized["root"]
    with store._connection() as conn:
        issuer = conn.execute("SELECT principal_type,status FROM principals WHERE principal_id=?", (normalized["operator_id"],)).fetchone()
        grantee = conn.execute("SELECT principal_type,status FROM principals WHERE principal_id=?", (normalized["runtime_id"],)).fetchone()
        anchor = conn.execute("SELECT policy_ref FROM trust_anchors WHERE principal_id=?", (normalized["operator_id"],)).fetchone()
    if not issuer or tuple(issuer) != ("HUMAN", "ACTIVE"):
        _fail("DAILY_TASK_OPERATOR_UNTRUSTED")
    if (not anchor or anchor["policy_ref"] != authority.policy["policy_version"]
            or normalized["operator_id"] not in authority.policy.get("trust_anchors", [])):
        _fail("DAILY_TASK_OPERATOR_UNTRUSTED")
    if not grantee or tuple(grantee) != ("SERVICE", "ACTIVE"):
        _fail("DAILY_TASK_RUNTIME_PRINCIPAL_INVALID")
    mode = RuntimeModeService(store, authority).current().get("mode")
    if mode != "NORMAL":
        _fail("DAILY_TASK_RUNTIME_MODE_UNSUPPORTED")
    if ParticipationModeService(store).current().get("mode") != NexusParticipationMode.ACTIVE.value:
        _fail("DAILY_TASK_PARTICIPATION_INACTIVE")
    now = _now()
    if not normalized["issued"] <= now < normalized["expires"]:
        _fail("DAILY_TASK_GRANT_NOT_CURRENT")
    if require_current_created_at and normalized["created"] > now:
        _fail("DAILY_TASK_CREATED_AT_NOT_CURRENT")


def _assert_ids_unused_or_replay(store, normalized: dict[str, Any], *, request_bound: bool, grant_replay: bool) -> None:
    root = normalized["root"]
    identifiers = [normalized["grant"]["grant_id"], root["task_id"], root["root_run_id"],
                   root["budget_account_id"], root["input_object_id"], root["contract_object_id"],
                   root["root_manifest_object_id"], *(root["classifications"][key]["assertion_id"] for key in _CLASS_KEYS)]
    with store._connection() as conn:
        command_rows = {row["command_id"] for row in conn.execute(
            "SELECT command_id FROM command_ledger WHERE command_id IN (%s)" % ",".join("?" for _ in normalized["command_ids"]),
            normalized["command_ids"],
        ).fetchall()}
        if not request_bound and command_rows:
            _fail("DAILY_TASK_COMMAND_ID_CONFLICT")
        if not request_bound:
            root_command = normalized["root_command_id"]
            checks = (
                ("delegation_grants", "grant_id", normalized["grant"]["grant_id"]),
                ("tasks", "task_id", root["task_id"]), ("runs", "run_id", root["root_run_id"]),
                ("budget_accounts", "account_id", root["budget_account_id"]),
                ("objects", "object_id", root["input_object_id"]),
                ("objects", "object_id", root["contract_object_id"]),
                ("objects", "object_id", root["root_manifest_object_id"]),
                *(("classification_assertions", "assertion_id", item["assertion_id"]) for item in root["classifications"].values()),
                ("logical_refs", "ref_id", "task-contract:" + root["task_id"]),
                *(("trace_events", "event_id", "evt-" + root_command + suffix) for suffix in (
                    "-root-create", "-trace-input", "-root-ready", "-root-running",
                )),
            )
            if any(conn.execute(f"SELECT 1 FROM {table} WHERE {column}=?", (value,)).fetchone() for table, column, value in checks):
                _fail("DAILY_TASK_LOGICAL_ID_CONFLICT")
            subjects = [(assertion["subject_type"], assertion["subject_ref"])
                        for assertion in root["classifications"].values()]
            if any(conn.execute("SELECT 1 FROM classification_assertions WHERE subject_type=? AND subject_ref=? LIMIT 1", subject).fetchone()
                   for subject in subjects):
                _fail("DAILY_TASK_LOGICAL_ID_CONFLICT")
            store._assert_unbarred_object_resources(conn, normalized["grant"]["resource_scope"])
        else:
            grant_row = conn.execute("SELECT * FROM delegation_grants WHERE grant_id=?", (normalized["grant"]["grant_id"],)).fetchone()
            if grant_replay != bool(grant_row):
                _fail("DAILY_TASK_GRANT_REPLAY_MISMATCH")
            if grant_row is not None:
                expected = normalized["grant"]
                stored = {
                    "grant_id": grant_row["grant_id"], "issued_by": grant_row["issued_by"],
                    "granted_to": grant_row["granted_to"], "task_scope": json.loads(grant_row["task_scope_json"]),
                    "resource_scope": json.loads(grant_row["resource_scope_json"]), "action_scope": json.loads(grant_row["action_scope_json"]),
                    "audience_scope": json.loads(grant_row["audience_scope_json"]), "issued_at": grant_row["issued_at"],
                    "expires_at": grant_row["expires_at"], "policy_version": grant_row["policy_version"],
                    "parent_grant_id": grant_row["parent_grant_id"],
                }
                exact = {key: expected.get(key) for key in stored}
                if stored != exact:
                    _fail("DAILY_TASK_GRANT_REPLAY_MISMATCH")
            task = conn.execute("SELECT requester_id,status,root_run_id FROM tasks WHERE task_id=?", (root["task_id"],)).fetchone()
            run = conn.execute("SELECT task_id,executor_kind,grant_id,parent_run_id FROM runs WHERE run_id=?", (root["root_run_id"],)).fetchone()
            if task and (task["requester_id"] != normalized["operator_id"] or task["root_run_id"] not in {None, root["root_run_id"]}):
                _fail("DAILY_TASK_LOGICAL_ID_CONFLICT")
            if run and (run["task_id"] != root["task_id"] or run["executor_kind"] != "ORCHESTRATOR"
                        or run["grant_id"] != normalized["grant"]["grant_id"] or run["parent_run_id"] is not None):
                _fail("DAILY_TASK_LOGICAL_ID_CONFLICT")
            budget = conn.execute("SELECT task_id,amount_limit,unit,model_call_limit,tool_call_limit,child_run_limit FROM budget_accounts WHERE account_id=?", (root["budget_account_id"],)).fetchone()
            if budget and (budget["task_id"] != root["task_id"] or budget["amount_limit"] != root["budget_limits"]["amount_limit"]
                           or budget["unit"] != root["budget_limits"]["unit"] or budget["model_call_limit"] != root["budget_limits"]["model_call_limit"]
                           or budget["tool_call_limit"] != root["budget_limits"]["tool_call_limit"] or budget["child_run_limit"] != root["budget_limits"]["child_run_limit"]):
                _fail("DAILY_TASK_LOGICAL_ID_CONFLICT")
            logical = conn.execute("SELECT current_object_id FROM logical_refs WHERE ref_id=?", ("task-contract:" + root["task_id"],)).fetchone()
            if logical and logical["current_object_id"] != root["contract_object_id"]:
                _fail("DAILY_TASK_LOGICAL_ID_CONFLICT")


def _summary(normalized: dict[str, Any]) -> dict[str, Any]:
    root = normalized["root"]
    grant = normalized["grant"]
    return {
        "task_id": root["task_id"], "root_run_id": root["root_run_id"],
        "grant_id": grant["grant_id"], "operator_principal_id": normalized["operator_id"],
        "runtime_principal_id": normalized["runtime_id"], "root_created_at": root["created_at"],
        "grant_issued_at": grant["issued_at"], "expires_at": grant["expires_at"],
        "action_scope": grant["action_scope"], "audience_scope": grant["audience_scope"],
        "resource_count": len(grant["resource_scope"]), "data_boundary": root["data_boundary"],
        "budget_limits": root["budget_limits"],
    }


def start_daily_task(
    *, store, authority, budget, trace, runtime, verifier, plan: dict[str, Any],
    confirmation: Callable[[str, dict[str, Any]], bool],
) -> dict[str, Any]:
    """Confirm, bind, grant, then create exactly one Root through HostedBridge."""
    try:
        if (authority.store is not store or budget.store is not store or trace.store is not store
                or runtime.store is not store or runtime.authority is not authority
                or runtime.budget is not budget or runtime.trace is not trace
                or verifier.store is not store or verifier.authority is not authority
                or authority.policy != store.policy):
            _fail("DAILY_TASK_COMPOSITION_INVALID")
        normalized = _validate_plan(store, authority, plan)
        _validate_binding(store, normalized["instance_expectation"])
        request_result = _replay(store, normalized["request_command_id"], "daily_task_start_request", normalized["request"])
        request_bound = request_result is not None
        grant_request_hash = store._request_hash("create_grant", normalized["grant"])
        with store._connection() as conn:
            grant_prior = store._replay_command(conn, normalized["grant_command_id"], "create_grant", grant_request_hash)
        grant_replay = grant_prior is not None
        _assert_ids_unused_or_replay(store, normalized, request_bound=request_bound, grant_replay=grant_replay)

        bridge = CodexHostedBridge(store=store, authority=authority, budget=budget, trace=trace,
                                   runtime=runtime, verifier=verifier)
        root_request = _bridge_request(store, normalized)
        root_request_hash = store._request_hash("create_hosted_task_root", root_request)
        with store._connection() as conn:
            completed_root = store._replay_command(conn, normalized["root_command_id"],
                                                   "create_hosted_task_root", root_request_hash)
        if request_bound and completed_root is not None:
            if (completed_root.get("task_id") != normalized["root"]["task_id"]
                    or completed_root.get("root_run_id") != normalized["root"]["root_run_id"]
                    or completed_root.get("executor_kind") != "ORCHESTRATOR"
                    or completed_root.get("status") != "RUNNING"):
                _fail("DAILY_TASK_ROOT_REPLAY_MISMATCH")
            return _result(normalized)

        _validate_current_authority(
            store, authority, normalized, require_current_created_at=not request_bound,
        )
        if not request_bound:
            expected = "START " + normalized["root"]["task_id"]
            if not confirmation(expected, _summary(normalized)):
                _fail("DAILY_TASK_CONFIRMATION_DENIED")
            # Confirmation may take long enough for the plan or issuer state to
            # change. Recheck immediately before crossing the first mutation.
            _validate_current_authority(store, authority, normalized, require_current_created_at=True)
        elif grant_replay and _grant_status(store, normalized["grant"]["grant_id"]) != "ACTIVE":
            # A committed Grant is never replaced or reactivated. Let the
            # bridge return an already-completed result above; partial work
            # cannot resume under terminal authority.
            _fail("DAILY_TASK_GRANT_UNAVAILABLE")

        # This is the first mutation. Its hash binds every semantic field while
        # keeping the actual user input out of CommandLedger.
        store.bind_command_request(command_id=normalized["request_command_id"],
                                   operation="daily_task_start_request", request=normalized["request"])
        if not grant_replay:
            if not normalized["issued"] <= _now() < normalized["expires"]:
                _fail("DAILY_TASK_GRANT_NOT_CURRENT")
            authority.create_grant(normalized["grant"], normalized["grant_command_id"])
        root = normalized["root"]
        result = bridge.create_task_root(
            command_id=normalized["root_command_id"], created_at=root["created_at"],
            task_id=root["task_id"], requester_id=normalized["operator_id"],
            grant_id=normalized["grant"]["grant_id"], root_run_id=root["root_run_id"],
            budget_account_id=root["budget_account_id"], budget_limits=root["budget_limits"],
            input_object_id=root["input_object_id"], input_payload=normalized["input_bytes"],
            task_contract=normalized["effective_contract"], contract_object_id=root["contract_object_id"],
            dag_nodes=[], root_manifest_object_id=root["root_manifest_object_id"],
            data_boundary=root["data_boundary"], classifications=root["classifications"],
        )
        if result.get("task_id") != root["task_id"] or result.get("root_run_id") != root["root_run_id"]:
            _fail("DAILY_TASK_ROOT_REPLAY_MISMATCH")
        return _result(normalized)
    except DailyTaskStartError:
        raise
    except CommandConflict:
        raise DailyTaskStartError("COMMAND_CONFLICT") from None
    except (InvalidDelegation, RuntimeDenied) as exc:
        reason = exc.args[0] if exc.args else "DAILY_TASK_START_DENIED"
        if isinstance(reason, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", reason):
            raise DailyTaskStartError(reason) from None
        raise DailyTaskStartError("DAILY_TASK_START_DENIED") from None
    except Exception:
        raise DailyTaskStartError("DAILY_TASK_START_FAILED") from None


def _grant_status(store, grant_id: str) -> str | None:
    with store._connection() as conn:
        row = conn.execute("SELECT status FROM delegation_grants WHERE grant_id=?", (grant_id,)).fetchone()
    return row["status"] if row else None


def _result(normalized: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "DAILY_TASK_STARTED", "task_id": normalized["root"]["task_id"],
        "root_run_id": normalized["root"]["root_run_id"], "grant_id": normalized["grant"]["grant_id"],
        "grant_expires_at": normalized["grant"]["expires_at"], "executor_kind": "ORCHESTRATOR",
        "run_status": "RUNNING",
    }
