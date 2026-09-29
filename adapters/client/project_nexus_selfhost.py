"""Narrow Project Nexus Stage 3 application composition.

This is a project-specific operator workflow over the existing Core services;
it is not a general workflow engine and does not initialize an instance.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any

from adapters.source.git import GitSourceImportError, import_git_source, inspect_local_git_head, prepare_git_source
from kernel.authority.errors import InvalidDelegation
from kernel.object.errors import CommandConflict
from kernel.participation import NexusParticipationMode, ParticipationModeService
from kernel.runtime.errors import RuntimeDenied
from kernel.runtime.modes import RuntimeModeService


PROTOCOL_VERSION = "project-nexus-selfhost-bootstrap-v2"
_LOGICAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_HEX_OID = re.compile(r"^[a-f0-9]+$")
_WORK_ACTIONS = {
    "RUN_CREATE", "RUN_TRANSITION", "TRACE_APPEND", "OBJECT_WRITE", "CLASSIFY",
    "VERIFY", "MEMORY_ADMIT", "INSPECT",
}
_SOURCE_IMPORT_SUFFIXES = (
    "request", "classify-artifact", "authorize-artifact", "artifact", "preflight-classify-artifact",
    "classify-artifact-authorize",
    "classify-evidence", "authorize-evidence", "evidence", "preflight-classify-evidence",
    "classify-evidence-authorize",
)
_ROOT_CHILD_SUFFIXES = (
    "request", "task", "budget-account", "class-root-run", "class-root-created", "root-create",
    "class-input", "authorize-input", "put-input", "class-input-event", "trace-input", "class-contract", "bind-contract",
    "create-dag", "class-root-manifest", "bind-root-manifest", "class-root-ready", "root-ready",
    "class-root-running", "root-running",
)
_ROOT_CLASS_SUBJECTS = {
    "root_run": ("RUN", "root_run_id"),
    "root_created_event": ("TRACE_EVENT", "root_create_event"),
    "input_object": ("OBJECT", "input_object_id"),
    "input_event": ("TRACE_EVENT", "input_event"),
    "task_contract": ("OBJECT", "contract_object_id"),
    "root_manifest": ("OBJECT", "root_manifest_object_id"),
    "root_ready_event": ("TRACE_EVENT", "root_ready_event"),
    "root_running_event": ("TRACE_EVENT", "root_running_event"),
}


class ProjectNexusSelfHostError(Exception):
    """Stable, sanitized application reason code."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


def _fail(reason: str) -> None:
    raise ProjectNexusSelfHostError(reason)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        _fail("STAGE3_MANIFEST_INVALID")
    return value


def _logical_id(value: Any) -> bool:
    return isinstance(value, str) and _LOGICAL_ID.fullmatch(value) is not None and not Path(value).is_absolute() and not PureWindowsPath(value).is_absolute() and not PureWindowsPath(value).drive


def _require_id(value: Any) -> str:
    if not _logical_id(value):
        _fail("STAGE3_MANIFEST_INVALID")
    return value


def _declared_id(value: Any, ids: set[str]) -> str:
    item = _require_id(value)
    if item in ids:
        _fail("STAGE3_DUPLICATE_LOGICAL_ID")
    ids.add(item)
    return item


def _unique_commands(commands: list[str], prefixes: list[str]) -> None:
    if len(commands) != len(set(commands)) or len(prefixes) != len(set(prefixes)):
        _fail("STAGE3_DUPLICATE_COMMAND_ID")


def _pairs_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def read_stage3_manifest(path: str | Path) -> dict[str, Any]:
    """Strictly parse a caller-frozen Stage 3 v2 manifest."""
    try:
        manifest = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_pairs_no_duplicates,
                              parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite number")))
    except Exception:
        raise ProjectNexusSelfHostError("STAGE3_MANIFEST_INVALID") from None
    if not isinstance(manifest, dict):
        _fail("STAGE3_MANIFEST_INVALID")
    return manifest


def _validate_manifest(manifest: Any, store) -> dict[str, Any]:
    top_keys = {
        "protocol_version", "stage3_command_id", "accepted_execution_sha", "instance_expectation",
        "control_grant", "work_grant", "root", "git_sources", "current_state", "claims",
        "verifications", "memory_candidates", "context_pack", "root_close",
    }
    doc = _object(manifest, top_keys, "top")
    if doc["protocol_version"] != PROTOCOL_VERSION:
        _fail("STAGE3_PROTOCOL_UNSUPPORTED")
    ids: set[str] = set()
    commands: list[str] = []
    prefixes: list[str] = []

    _declared_id(doc["stage3_command_id"], ids)
    commands.append(doc["stage3_command_id"])
    accepted = doc["accepted_execution_sha"]
    if not isinstance(accepted, str) or _HEX_OID.fullmatch(accepted) is None or len(accepted) not in {40, 64}:
        _fail("STAGE3_MANIFEST_INVALID")

    instance = _object(doc["instance_expectation"], {"instance_id", "policy_version", "policy_sha256", "journal_identity"}, "instance")
    for key in ("instance_id", "policy_version"):
        _require_id(instance[key])
    for key in ("policy_sha256", "journal_identity"):
        if not isinstance(instance[key], str) or _SHA256.fullmatch(instance[key]) is None:
            _fail("STAGE3_MANIFEST_INVALID")

    control = _object(doc["control_grant"], {"grant_id", "revoke_command_id"}, "control")
    _declared_id(control["grant_id"], ids)
    _declared_id(control["revoke_command_id"], ids)
    commands.append(control["revoke_command_id"])

    work = _object(doc["work_grant"], {"grant", "create_command_id", "revoke_command_id"}, "work")
    store._validate("nexus.delegation_grant@1.schema.json", work["grant"])
    _declared_id(work["grant"]["grant_id"], ids)
    for key in ("create_command_id", "revoke_command_id"):
        _declared_id(work[key], ids)
        commands.append(work[key])
    if work["grant"].get("parent_grant_id") is not None or "parent_grant_id" in work["grant"]:
        _fail("STAGE3_WORK_GRANT_UNSAFE")

    root_keys = {
        "created_at", "create_task_root_command_id", "task_id", "requester_id", "grant_id", "root_run_id",
        "budget_account_id", "budget_limits", "input_object_id", "input_payload", "task_contract",
        "contract_object_id", "dag_nodes", "root_manifest_object_id", "data_boundary", "classifications",
    }
    root = _object(doc["root"], root_keys, "root")
    for key in ("create_task_root_command_id", "task_id", "root_run_id", "budget_account_id",
                "input_object_id", "contract_object_id", "root_manifest_object_id"):
        _declared_id(root[key], ids)
    _require_id(root["requester_id"])
    _require_id(root["grant_id"])
    commands.extend(root["create_task_root_command_id"] + "-" + suffix for suffix in _ROOT_CHILD_SUFFIXES)
    prefixes.append(root["create_task_root_command_id"])
    if root["grant_id"] != work["grant"]["grant_id"]:
        _fail("STAGE3_MANIFEST_INVALID")
    _validate_timestamp(root["created_at"])
    if not isinstance(root["input_payload"], str):
        _fail("STAGE3_MANIFEST_INVALID")
    root["input_payload"].encode("utf-8", errors="strict")
    if not isinstance(root["task_contract"], dict) or not isinstance(root["dag_nodes"], list):
        _fail("STAGE3_MANIFEST_INVALID")
    limits = root["budget_limits"]
    if not isinstance(limits, dict) or set(limits) != {"amount_limit", "unit", "model_call_limit", "tool_call_limit", "child_run_limit"}:
        _fail("STAGE3_MANIFEST_INVALID")
    if any(type(limits[key]) is not int or limits[key] < 0 for key in ("amount_limit", "model_call_limit", "tool_call_limit", "child_run_limit")) or not isinstance(limits["unit"], str) or not limits["unit"]:
        _fail("STAGE3_MANIFEST_INVALID")
    if not isinstance(root["data_boundary"], dict):
        _fail("STAGE3_MANIFEST_INVALID")
    classes = _object(root["classifications"], set(_ROOT_CLASS_SUBJECTS), "root classifications")
    root_command = root["create_task_root_command_id"]
    expected_class_refs = {
        "root_run": root["root_run_id"],
        "root_created_event": "evt-" + root_command + "-root-create",
        "input_object": root["input_object_id"],
        "input_event": "evt-" + root_command + "-trace-input",
        "task_contract": root["contract_object_id"],
        "root_manifest": root["root_manifest_object_id"],
        "root_ready_event": "evt-" + root_command + "-root-ready",
        "root_running_event": "evt-" + root_command + "-root-running",
    }
    for key, (subject_type, _) in _ROOT_CLASS_SUBJECTS.items():
        assertion = _object(classes[key], {"schema_id", "schema_version", "assertion_id", "subject_type", "subject_ref", "sensitivity_level", "handling_tags", "policy_version", "reason", "actor_id"}, "classification")
        _declared_id(assertion["assertion_id"], ids)
        _validate_classification(assertion, subject_type, expected_class_refs[key], instance["policy_version"], work["grant"]["granted_to"], root["data_boundary"])

    sources = doc["git_sources"]
    if not isinstance(sources, list) or len(sources) != 8:
        _fail("STAGE3_GIT_SOURCE_COUNT_INVALID")
    artifact_ids: list[str] = []
    evidence_ids: list[str] = []
    git_plans: list[dict[str, Any]] = []
    for item in sources:
        entry = _object(item, {"expected_git_object_format", "expected_blob_oid", "import_plan"}, "git source")
        if (entry["expected_git_object_format"] not in {"sha1", "sha256"}
                or not isinstance(entry["expected_blob_oid"], str)
                or len(entry["expected_blob_oid"]) != {"sha1": 40, "sha256": 64}[entry["expected_git_object_format"]]
                or _HEX_OID.fullmatch(entry["expected_blob_oid"]) is None):
            _fail("STAGE3_MANIFEST_INVALID")
        plan = entry["import_plan"]
        plan_keys = {"repository_id", "commit_oid", "path", "task_id", "run_id", "grant_id", "artifact_object_id",
                     "artifact_classification_assertion_id", "evidence_object_id", "evidence_classification_assertion_id",
                     "sensitivity_level", "handling_tags", "command_id_prefix"}
        plan = _object(plan, plan_keys, "git plan")
        for key in ("repository_id", "commit_oid", "path", "task_id", "run_id", "grant_id", "artifact_object_id",
                    "artifact_classification_assertion_id", "evidence_object_id", "evidence_classification_assertion_id",
                    "sensitivity_level", "command_id_prefix"):
            if not isinstance(plan[key], str) or not plan[key]:
                _fail("STAGE3_MANIFEST_INVALID")
        _declared_id(plan["artifact_object_id"], ids)
        _declared_id(plan["evidence_object_id"], ids)
        _declared_id(plan["artifact_classification_assertion_id"], ids)
        _declared_id(plan["evidence_classification_assertion_id"], ids)
        if plan["task_id"] != root["task_id"] or plan["run_id"] != root["root_run_id"] or plan["grant_id"] != work["grant"]["grant_id"]:
            _fail("STAGE3_MANIFEST_INVALID")
        if len(plan["commit_oid"]) != {"sha1": 40, "sha256": 64}[entry["expected_git_object_format"]] or _HEX_OID.fullmatch(plan["commit_oid"]) is None:
            _fail("STAGE3_MANIFEST_INVALID")
        if not _logical_id(plan["repository_id"]):
            _fail("STAGE3_MANIFEST_INVALID")
        if (not isinstance(plan["handling_tags"], list)
                or any(not isinstance(tag, str) or not tag for tag in plan["handling_tags"])
                or len(plan["handling_tags"]) != len(set(plan["handling_tags"]))):
            _fail("STAGE3_MANIFEST_INVALID")
        _validate_relative_git_path(plan["path"])
        prefix = _require_id(plan["command_id_prefix"])
        if len(prefix) > 96:
            _fail("STAGE3_MANIFEST_INVALID")
        prefixes.append(prefix)
        commands.extend(prefix + ":" + suffix for suffix in _SOURCE_IMPORT_SUFFIXES)
        artifact_ids.append(plan["artifact_object_id"])
        evidence_ids.append(plan["evidence_object_id"])
        git_plans.append(plan)

    state = _object(doc["current_state"], {"object_id", "payload", "source_refs", "classification_assertion", "classify_command_id", "put_command_id"}, "current state")
    _declared_id(state["object_id"], ids)
    if state["object_id"] != "artifact-project-nexus-current-state-v1" or not isinstance(state["payload"], dict):
        _fail("STAGE3_MANIFEST_INVALID")
    if not isinstance(state["source_refs"], list) or len(state["source_refs"]) == 0 or len(state["source_refs"]) != len(set(state["source_refs"])):
        _fail("STAGE3_MANIFEST_INVALID")
    _validate_object_class(state["classification_assertion"], state["object_id"], instance["policy_version"], work["grant"]["granted_to"], root["data_boundary"], ids)
    for key in ("classify_command_id", "put_command_id"):
        _declared_id(state[key], ids)
        commands.append(state[key])

    claims = doc["claims"]
    if not isinstance(claims, list) or len(claims) != 5:
        _fail("STAGE3_CLAIM_COUNT_INVALID")
    claim_ids: list[str] = []
    for claim in claims:
        claim = _object(claim, {"claim_id", "payload", "evidence_refs", "classification_assertion", "classify_command_id", "put_command_id"}, "claim")
        _declared_id(claim["claim_id"], ids)
        if not isinstance(claim["payload"], dict) or not isinstance(claim["evidence_refs"], list) or not claim["evidence_refs"] or len(claim["evidence_refs"]) != len(set(claim["evidence_refs"])):
            _fail("STAGE3_MANIFEST_INVALID")
        _validate_object_class(claim["classification_assertion"], claim["claim_id"], instance["policy_version"], work["grant"]["granted_to"], root["data_boundary"], ids)
        for key in ("classify_command_id", "put_command_id"):
            _declared_id(claim[key], ids)
            commands.append(claim[key])
        claim_ids.append(claim["claim_id"])

    verifications = doc["verifications"]
    if not isinstance(verifications, list) or len(verifications) != 14:
        _fail("STAGE3_VERIFICATION_COUNT_INVALID")
    verification_ids: list[str] = []
    for item in verifications:
        item = _object(item, {"verification_id", "target_ref", "evidence_refs"}, "verification")
        _declared_id(item["verification_id"], ids)
        if not _logical_id(item["target_ref"]) or not isinstance(item["evidence_refs"], list) or not item["evidence_refs"] or len(item["evidence_refs"]) != len(set(item["evidence_refs"])):
            _fail("STAGE3_MANIFEST_INVALID")
        verification_ids.append(item["verification_id"])
        commands.append("verify-" + item["verification_id"])

    candidates = doc["memory_candidates"]
    if not isinstance(candidates, list) or len(candidates) != 5:
        _fail("STAGE3_MEMORY_CANDIDATE_COUNT_INVALID")
    candidate_ids: list[str] = []
    for candidate in candidates:
        candidate = _object(candidate, {"candidate_id", "claim_ref", "evidence_refs", "verification_ref", "owner", "review_trigger", "classification_assertion_ref", "command_id"}, "memory candidate")
        for key in ("candidate_id", "command_id"):
            _declared_id(candidate[key], ids)
            commands.append(candidate[key])
        for key in ("claim_ref", "verification_ref", "classification_assertion_ref"):
            _require_id(candidate[key])
        if (not isinstance(candidate["owner"], str) or not candidate["owner"]
                or not isinstance(candidate["review_trigger"], str) or not candidate["review_trigger"]):
            _fail("STAGE3_MANIFEST_INVALID")
        if not isinstance(candidate["evidence_refs"], list) or not candidate["evidence_refs"] or len(candidate["evidence_refs"]) != len(set(candidate["evidence_refs"])):
            _fail("STAGE3_MANIFEST_INVALID")
        candidate_ids.append(candidate["candidate_id"])

    context = _object(doc["context_pack"], {"pack_object_id", "classification_assertion", "source_refs", "memory_query", "classification_command_id", "command_id"}, "context pack")
    _declared_id(context["pack_object_id"], ids)
    _declared_id(context["classification_command_id"], ids)
    commands.append(context["classification_command_id"])
    _declared_id(context["command_id"], ids)
    commands.append(context["command_id"])
    if context["memory_query"] is not None or not isinstance(context["source_refs"], list) or not context["source_refs"] or len(context["source_refs"]) != len(set(context["source_refs"])):
        _fail("STAGE3_CONTEXT_PLAN_INVALID")
    _validate_object_class(context["classification_assertion"], context["pack_object_id"], instance["policy_version"], work["grant"]["granted_to"], root["data_boundary"], ids)
    for ref in context["source_refs"]:
        _require_id(ref)
    required_context = {state["object_id"], "src-phase6-closure-v1", "src-utility-pilot-freeze-v1",
                        "src-selfhost-design-v1", "src-fresh-bootstrap-guide-v1", "src-git-source-import-guide-v1"}
    if set(context["source_refs"]) != required_context:
        _fail("STAGE3_CONTEXT_PLAN_INVALID")

    close = _object(doc["root_close"], {"verifying", "succeeded"}, "root close")
    for state_name, transition in (("verifying", "VERIFYING"), ("succeeded", "SUCCEEDED")):
        item = _object(close[state_name], {"command_id", "classification_command_id", "classification_assertion"}, "root transition")
        _declared_id(item["command_id"], ids)
        _declared_id(item["classification_command_id"], ids)
        commands.append(item["command_id"])
        commands.append(item["classification_command_id"])
        _validate_classification(item["classification_assertion"], "TRACE_EVENT", "evt-" + item["command_id"], instance["policy_version"], work["grant"]["granted_to"], root["data_boundary"])
        _declared_id(item["classification_assertion"]["assertion_id"], ids)

    if len(doc["stage3_command_id"] + ":request") > 128 or len(doc["stage3_command_id"] + ":ready-to-close") > 128:
        _fail("STAGE3_MANIFEST_INVALID")
    commands.extend([doc["stage3_command_id"] + ":request", doc["stage3_command_id"] + ":ready-to-close"])
    prefixes.append(doc["stage3_command_id"])
    if any(len(command_id) > 128 for command_id in commands):
        _fail("STAGE3_MANIFEST_INVALID")
    _unique_commands(commands, prefixes)

    # Cross-record IDs and refs are checked only after every list has a strict,
    # bounded shape, so errors remain deterministic and path-free.
    if root["requester_id"] != work["grant"]["issued_by"] or root["task_id"] not in work["grant"]["task_scope"]:
        _fail("STAGE3_MANIFEST_INVALID")
    if not set(state["source_refs"]).issubset(set(artifact_ids)):
        _fail("STAGE3_MANIFEST_INVALID")
    expected_verification_targets = set(artifact_ids) | {state["object_id"]} | set(claim_ids)
    if (len({item["target_ref"] for item in verifications}) != 14
            or {item["target_ref"] for item in verifications} != expected_verification_targets):
        _fail("STAGE3_VERIFICATION_BINDING_INVALID")
    for item in verifications:
        if item["target_ref"] not in ({state["object_id"]} | set(claim_ids) | set(artifact_ids)):
            _fail("STAGE3_VERIFICATION_BINDING_INVALID")
        if not set(item["evidence_refs"]).issubset(set(artifact_ids) | set(evidence_ids)):
            _fail("STAGE3_VERIFICATION_BINDING_INVALID")
    claim_map = {item["claim_id"]: item for item in claims}
    verification_map = {item["target_ref"]: item for item in verifications}
    source_evidence = {item["import_plan"]["artifact_object_id"]: item["import_plan"]["evidence_object_id"] for item in sources}
    for target_ref, evidence_ref in source_evidence.items():
        if verification_map[target_ref]["evidence_refs"] != [evidence_ref]:
            _fail("STAGE3_VERIFICATION_BINDING_INVALID")
    if verification_map[state["object_id"]]["evidence_refs"] != sorted(evidence_ids):
        _fail("STAGE3_VERIFICATION_BINDING_INVALID")
    for claim in claims:
        if (not set(claim["evidence_refs"]).issubset(set(evidence_ids))
                or claim["evidence_refs"] != sorted(claim["evidence_refs"])):
            _fail("STAGE3_VERIFICATION_BINDING_INVALID")
        if verification_map[claim["claim_id"]]["evidence_refs"] != claim["evidence_refs"]:
            _fail("STAGE3_VERIFICATION_BINDING_INVALID")
    for candidate in candidates:
        if candidate["claim_ref"] not in claim_map or candidate["claim_ref"] != verification_map[candidate["claim_ref"]]["target_ref"]:
            _fail("STAGE3_MEMORY_BINDING_INVALID")
        if candidate["verification_ref"] != verification_map[candidate["claim_ref"]]["verification_id"] or candidate["evidence_refs"] != verification_map[candidate["claim_ref"]]["evidence_refs"]:
            _fail("STAGE3_MEMORY_BINDING_INVALID")
        if candidate["classification_assertion_ref"] != claim_map[candidate["claim_ref"]]["classification_assertion"]["assertion_id"]:
            _fail("STAGE3_MEMORY_BINDING_INVALID")
    _validate_work_grant(work["grant"], doc, store)
    return doc


def _validate_timestamp(value: Any) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", value):
        _fail("STAGE3_MANIFEST_INVALID")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except ValueError:
        _fail("STAGE3_MANIFEST_INVALID")
    if parsed.isoformat(timespec="microseconds").replace("+00:00", "Z") != value:
        _fail("STAGE3_MANIFEST_INVALID")


def _validate_relative_git_path(value: Any) -> None:
    if (not isinstance(value, str) or not value or "\x00" in value or "\\" in value
            or value.startswith("/") or PureWindowsPath(value).is_absolute()
            or PureWindowsPath(value).drive or any(part in {"", ".", ".."} for part in value.split("/"))):
        _fail("STAGE3_MANIFEST_INVALID")


def _validate_classification(assertion: dict[str, Any], subject_type: str, subject_ref: str,
                              policy_version: str, actor_id: str, boundary: dict[str, Any]) -> None:
    if (assertion.get("schema_id") != "nexus.classification_assertion" or assertion.get("schema_version") != 1
            or assertion.get("subject_type") != subject_type or assertion.get("subject_ref") != subject_ref
            or assertion.get("policy_version") != policy_version or assertion.get("actor_id") != actor_id
            or not isinstance(assertion.get("handling_tags"), list)
            or assertion.get("sensitivity_level") not in set(boundary.get("allowed_classifications", []))
            or not set(assertion.get("handling_tags", [])).issubset(set(boundary.get("handling_tags", [])))):
        _fail("STAGE3_CLASSIFICATION_INVALID")


def _validate_object_class(assertion: Any, object_id: str, policy_version: str, actor_id: str,
                           boundary: dict[str, Any], ids: set[str]) -> None:
    keys = {"schema_id", "schema_version", "assertion_id", "subject_type", "subject_ref", "sensitivity_level", "handling_tags", "policy_version", "reason", "actor_id"}
    assertion = _object(assertion, keys, "object classification")
    _declared_id(assertion["assertion_id"], ids)
    _validate_classification(assertion, "OBJECT", object_id, policy_version, actor_id, boundary)


def _validate_work_grant(grant: dict[str, Any], manifest: dict[str, Any], store) -> None:
    root = manifest["root"]
    if (grant.get("status") != "ACTIVE" or grant.get("issued_by") != root["requester_id"]
            or grant.get("granted_to") != manifest["work_grant"]["grant"]["granted_to"]
            or grant.get("task_scope") != [root["task_id"]]
            or set(grant.get("action_scope", [])) != _WORK_ACTIONS
            or set(grant.get("audience_scope", [])) != {"nexus-runtime", "nexus-inspect"}
            or grant.get("policy_version") != manifest["instance_expectation"]["policy_version"]
            or grant.get("credential_ref") not in (None, "")):
        _fail("STAGE3_WORK_GRANT_UNSAFE")
    if any(value == "*" for field in ("task_scope", "resource_scope", "action_scope", "audience_scope") for value in grant.get(field, [])):
        _fail("STAGE3_WORK_GRANT_UNSAFE")
    try:
        issued = _parse_time(grant["issued_at"])
        expires = _parse_time(grant["expires_at"])
    except (KeyError, TypeError, ValueError):
        _fail("STAGE3_WORK_GRANT_UNSAFE")
    if issued >= expires or grant["issued_at"] == grant["expires_at"]:
        _fail("STAGE3_WORK_GRANT_UNSAFE")
    required_resources = _required_resources(manifest)
    if set(grant.get("resource_scope", [])) != required_resources:
        _fail("STAGE3_WORK_GRANT_RESOURCE_CLOSURE_MISMATCH")
    store._validate("nexus.delegation_grant@1.schema.json", grant)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone")
    return parsed.astimezone(timezone.utc)


def _required_resources(manifest: dict[str, Any]) -> set[str]:
    root = manifest["root"]
    task_id = root["task_id"]
    resources = {"task:" + task_id, root["root_run_id"], root["input_object_id"], root["contract_object_id"], root["root_manifest_object_id"]}
    object_refs = {root["input_object_id"], root["contract_object_id"], root["root_manifest_object_id"]}
    event_refs = set()
    for assertion in root["classifications"].values():
        if assertion["subject_type"] == "TRACE_EVENT":
            event_refs.add(assertion["subject_ref"])
        elif assertion["subject_type"] == "OBJECT":
            object_refs.add(assertion["subject_ref"])
        else:
            resources.add(assertion["subject_ref"])
    for item in manifest["git_sources"]:
        plan = item["import_plan"]
        object_refs.update({plan["artifact_object_id"], plan["evidence_object_id"]})
    state = manifest["current_state"]
    object_refs.add(state["object_id"])
    object_refs.update(state["source_refs"])
    for claim in manifest["claims"]:
        object_refs.add(claim["claim_id"])
        object_refs.update(claim["evidence_refs"])
    for verification in manifest["verifications"]:
        object_refs.add(verification["target_ref"])
        object_refs.update(verification["evidence_refs"])
    for candidate in manifest["memory_candidates"]:
        resources.add(candidate["candidate_id"])
        object_refs.add(candidate["claim_ref"])
        object_refs.update(candidate["evidence_refs"])
    context = manifest["context_pack"]
    object_refs.add(context["pack_object_id"])
    object_refs.update(context["source_refs"])
    if context["classification_assertion"]["subject_type"] == "OBJECT":
        object_refs.add(context["classification_assertion"]["subject_ref"])
    for close in manifest["root_close"].values():
        event_refs.add(close["classification_assertion"]["subject_ref"])
    resources.update(object_refs)
    resources.update(event_refs)
    resources.update("object:" + ref for ref in context["source_refs"])
    return resources


def _semantic_request(manifest: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(manifest))
    result["root"]["input_payload"] = {"sha256": _sha256(result["root"]["input_payload"].encode("utf-8")), "byte_size": len(result["root"]["input_payload"].encode("utf-8"))}
    result["current_state"]["payload"] = {"sha256": _sha256(_canonical_bytes(result["current_state"]["payload"]))}
    for claim in result["claims"]:
        claim["payload"] = {"sha256": _sha256(_canonical_bytes(claim["payload"]))}
    return result


def _check_instance_binding(store, expected: dict[str, Any]) -> None:
    actual = store.get_instance_binding_status()
    if (actual.get("state") not in {"FRESH_BOUND_INSTANCE", "LEGACY_ADOPTED_BOUND_INSTANCE"}
            or actual.get("policy_content_binding") != "BOUND"
            or actual.get("instance_id") != expected["instance_id"]
            or actual.get("policy_version") != expected["policy_version"]
            or actual.get("policy_sha256") != expected["policy_sha256"]
            or actual.get("journal_identity") != expected["journal_identity"]):
        _fail("STAGE3_INSTANCE_BINDING_MISMATCH")


def _preflight(*, store, authority, participation, runtime_mode, repo_path: str | Path, manifest: dict[str, Any]) -> None:
    _check_instance_binding(store, manifest["instance_expectation"])
    runtime_mode.current()
    mode = participation.current().get("mode")
    if mode not in {item.value for item in NexusParticipationMode}:
        _fail("STAGE3_PARTICIPATION_STATE_UNAVAILABLE")
    grant = manifest["work_grant"]["grant"]
    grant_command = manifest["work_grant"]["create_command_id"]
    with store._connection() as conn:
        grant_command_prior = conn.execute("SELECT 1 FROM command_ledger WHERE command_id=?", (grant_command,)).fetchone()
        if not grant_command_prior:
            issuer = conn.execute("SELECT principal_type,status FROM principals WHERE principal_id=?", (grant["issued_by"],)).fetchone()
            grantee = conn.execute("SELECT principal_type,status FROM principals WHERE principal_id=?", (grant["granted_to"],)).fetchone()
            anchor = conn.execute("SELECT policy_ref FROM trust_anchors WHERE principal_id=?", (grant["issued_by"],)).fetchone()
            policy = store.policy
            if (not issuer or tuple(issuer) != ("HUMAN", "ACTIVE")
                    or not grantee or tuple(grantee) != ("SERVICE", "ACTIVE")
                    or not anchor or anchor["policy_ref"] != policy.get("policy_version")
                    or grant["issued_by"] not in policy.get("trust_anchors", [])):
                _fail("STAGE3_WORK_GRANT_UNSAFE")
    head = inspect_local_git_head(repo_path)
    accepted = manifest["accepted_execution_sha"].lower()
    if head["head_oid"] != accepted or len(accepted) != {"sha1": 40, "sha256": 64}.get(head["git_object_format"]):
        _fail("STARTING_COMMIT_MISMATCH")
    for source in manifest["git_sources"]:
        plan = source["import_plan"]
        identity = prepare_git_source(repo_path, commit_oid=plan["commit_oid"], path=plan["path"])
        if (identity["git_object_format"] != source["expected_git_object_format"]
                or identity["commit_oid"] != plan["commit_oid"].lower()
                or identity["blob_oid"] != source["expected_blob_oid"]):
            _fail("STAGE3_GIT_SOURCE_IDENTITY_MISMATCH")


def _ledger_replay(store, command_id: str, operation: str, request: dict[str, Any]) -> dict[str, Any] | None:
    digest = store._request_hash(operation, request)
    with store._connection() as conn:
        return store._replay_command(conn, command_id, operation, digest)


def _persist_stage3_complete(store, command_id: str, request_hash: str, result: dict[str, Any]) -> dict[str, Any]:
    operation = "project_nexus_selfhost_bootstrap"
    with store._lock, store._connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            prior = store._replay_command(conn, command_id, operation, request_hash)
            if prior is not None:
                if prior != result:
                    raise CommandConflict("COMMAND_CONFLICT")
                conn.commit()
                return prior
            store._record_command(conn, command_id, operation, request_hash, result)
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise


def _grant_state(store, grant_id: str) -> tuple[str, str, str] | None:
    with store._connection() as conn:
        row = conn.execute("SELECT status,issued_at,expires_at FROM delegation_grants WHERE grant_id=?", (grant_id,)).fetchone()
    return tuple(row) if row else None


def _require_work_grant_current(authority, grant: dict[str, Any], *, terminal_recovery: bool = False) -> None:
    now = datetime.now(timezone.utc)
    try:
        issued = _parse_time(grant["issued_at"])
        expires = _parse_time(grant["expires_at"])
        if issued > now or expires <= now:
            _fail("STAGE3_TERMINAL_AUTHORITY_UNAVAILABLE" if terminal_recovery else "STAGE3_WORK_GRANT_EXPIRED")
        authority.validate_delegation_chain(grant["grant_id"])
    except ProjectNexusSelfHostError:
        raise
    except InvalidDelegation as exc:
        message = exc.args[0] if exc.args else ""
        if message in {"GRANT_NOT_ACTIVE", "GRANT_NOT_CURRENTLY_VALID", "GRANT_NOT_FOUND"}:
            _fail("STAGE3_TERMINAL_AUTHORITY_UNAVAILABLE" if terminal_recovery else "STAGE3_WORK_GRANT_UNAVAILABLE")
        raise


def _transition_request(run_id: str, expected_state: str, next_state: str, assertion_ref: str) -> dict[str, Any]:
    return {"run_id": run_id, "expected_state": expected_state, "next_state": next_state,
            "classification_assertion_ref": assertion_ref}


def _record_classification(authority, *, classification: dict[str, Any], grant_id: str,
                           task_id: str, command_id: str) -> None:
    authority.record_classification_assertion(classification, grant_id=grant_id, task_id=task_id,
                                              audience="nexus-runtime", command_id=command_id)


def _put_canonical_object(*, store, authority, grant_id: str, task_id: str,
                          run_id: str, classification_command_id: str, object_command_id: str,
                          object_id: str, payload: bytes,
                          object_type: str, classification: dict[str, Any], derived_from: list[str]) -> None:
    _record_classification(authority, classification=classification, grant_id=grant_id, task_id=task_id,
                           command_id=classification_command_id)
    authority.evaluate_authorization(grant_id, {"task": task_id, "resource": object_id,
        "action": "OBJECT_WRITE", "audience": "nexus-runtime"}, object_command_id + "-authorize")
    store.put_object(command_id=object_command_id, object_id=object_id, payload=payload,
                     object_type=object_type, created_by_run=run_id,
                     classification_assertion_ref=classification["assertion_id"], derived_from=derived_from)


def _assert_current_projection(*, runtime, store, authority, manifest: dict[str, Any], imported: list[dict[str, Any]],
                               verifications: list[dict[str, Any]], candidates: list[dict[str, Any]], context_status: dict[str, Any]) -> None:
    root = manifest["root"]
    grant_id = manifest["work_grant"]["grant"]["grant_id"]
    projection = runtime.inspect_task(grant_id=grant_id, task_id=root["task_id"])
    root_projection = next((item for item in projection.get("runs", []) if item.get("run_id") == root["root_run_id"]), None)
    if (projection.get("task", {}).get("task_id") != root["task_id"]
            or projection.get("task", {}).get("status") != "ACTIVE"
            or not root_projection or root_projection.get("status") != "VERIFYING"
            or root_projection.get("executor_kind") != "ORCHESTRATOR"
            or root_projection.get("parent_run_id") is not None):
        _fail("STAGE3_FINAL_PROJECTION_MISMATCH")
    if len(imported) != 8 or any(item.get("status") != "IMPORTED" for item in imported):
        _fail("STAGE3_FINAL_PROJECTION_MISMATCH")
    if len(verifications) != 14 or any(item.get("verdict") != "PASS" or item.get("verifier_kind") != "T1_DETERMINISTIC" for item in verifications):
        _fail("STAGE3_FINAL_PROJECTION_MISMATCH")
    if len(candidates) != 5 or any(item.get("truth_state") != "INFERRED" or item.get("status") != "QUARANTINED" for item in candidates):
        _fail("STAGE3_MEMORY_TRUTH_POLICY_MISMATCH")
    context = manifest["context_pack"]
    if (context_status.get("status") != "PACK_COMPILED"
            or context_status.get("model_visible_exposure") != "UNKNOWN"
            or context_status.get("task_id") != root["task_id"]
            or context_status.get("run_id") != root["root_run_id"]
            or set(context_status.get("selected_refs", [])) != set(context["source_refs"])):
        _fail("STAGE3_FINAL_PROJECTION_MISMATCH")
    state_meta = store.get_object_metadata(manifest["current_state"]["object_id"])
    if state_meta.get("payload_state") != "AVAILABLE" or state_meta.get("object_type") != "artifact":
        _fail("STAGE3_FINAL_PROJECTION_MISMATCH")
    for claim in manifest["claims"]:
        claim_meta = store.get_object_metadata(claim["claim_id"])
        if claim_meta.get("payload_state") != "AVAILABLE" or claim_meta.get("object_type") != "claim":
            _fail("STAGE3_FINAL_PROJECTION_MISMATCH")
    for plan in ([item["import_plan"] for item in manifest["git_sources"]]):
        meta = store.get_object_metadata(plan["artifact_object_id"])
        if meta.get("payload_state") != "AVAILABLE" or not store.verify_object(plan["artifact_object_id"]):
            _fail("STAGE3_FINAL_PROJECTION_MISMATCH")


def _ready_request(manifest: dict[str, Any], master_hash: str) -> dict[str, Any]:
    return {
        "master_request_hash": master_hash,
        "task_id": manifest["root"]["task_id"],
        "root_run_id": manifest["root"]["root_run_id"],
        "work_grant_id": manifest["work_grant"]["grant"]["grant_id"],
        "context_pack_id": manifest["context_pack"]["pack_object_id"],
        "source_refs": sorted(plan["import_plan"]["artifact_object_id"] for plan in manifest["git_sources"]),
        "evidence_refs": sorted(plan["import_plan"]["evidence_object_id"] for plan in manifest["git_sources"]),
        "current_state_id": manifest["current_state"]["object_id"],
        "claim_ids": sorted(item["claim_id"] for item in manifest["claims"]),
        "verification_ids": sorted(item["verification_id"] for item in manifest["verifications"]),
        "memory_candidate_ids": sorted(item["candidate_id"] for item in manifest["memory_candidates"]),
        "pre_terminal_projection": {"root_status": "VERIFYING", "context_status": "PACK_COMPILED", "model_visible_exposure": "UNKNOWN"},
    }


def _record_ready(store, command_id: str, request: dict[str, Any]) -> None:
    store.bind_command_request(command_id=command_id, operation="project_nexus_selfhost_ready_to_close", request=request)


def _finish_terminal(*, store, authority, trace, manifest: dict[str, Any], master_hash: str,
                     terminal_replay: dict[str, Any] | None) -> dict[str, Any]:
    root = manifest["root"]
    stage_id = manifest["stage3_command_id"]
    grant = manifest["work_grant"]["grant"]
    ready_id = stage_id + ":ready-to-close"
    ready_req = _ready_request(manifest, master_hash)
    ready = _ledger_replay(store, ready_id, "project_nexus_selfhost_ready_to_close", ready_req)
    if ready is None:
        if terminal_replay is not None:
            _fail("STAGE3_READY_TO_CLOSE_MISSING")
        return {}
    if ready != {"status": "REQUEST_BOUND"}:
        _fail("STAGE3_READY_TO_CLOSE_INVALID")

    close_succeeded = manifest["root_close"]["succeeded"]
    transition_req = _transition_request(root["root_run_id"], "VERIFYING", "SUCCEEDED",
                                         close_succeeded["classification_assertion"]["assertion_id"])
    expected_hash = store._request_hash("transition_run", transition_req)
    if terminal_replay is not None:
        if terminal_replay.get("run_id") != root["root_run_id"] or terminal_replay.get("status") != "SUCCEEDED":
            _fail("STAGE3_TERMINAL_PROJECTION_MISMATCH")
        # _ledger_replay has already matched command ID, operation, and exact
        # immutable request hash. Reuse that committed result without entering
        # the transition service's current runtime/authority gates.
        result = terminal_replay
    else:
        try:
            _require_work_grant_current(authority, grant, terminal_recovery=True)
        except ProjectNexusSelfHostError as exc:
            _fail("STAGE3_TERMINAL_AUTHORITY_UNAVAILABLE")
        result = trace.transition_run(command_id=close_succeeded["command_id"], **transition_req)
        if result.get("status") != "SUCCEEDED" or store._request_hash("transition_run", transition_req) != expected_hash:
            _fail("STAGE3_TERMINAL_PROJECTION_MISMATCH")

    grant_state = _grant_state(store, grant["grant_id"])
    if not grant_state:
        _fail("STAGE3_TERMINAL_AUTHORITY_UNAVAILABLE")
    if grant_state[0] == "ACTIVE":
        authority.revoke_grant(grant["grant_id"], manifest["work_grant"]["revoke_command_id"])
    elif grant_state[0] != "REVOKED":
        _fail("STAGE3_TERMINAL_AUTHORITY_UNAVAILABLE")
    final = {
        "status": "STAGE3_CANONICAL_COMPLETE",
        "instance_id": manifest["instance_expectation"]["instance_id"],
        "task_id": root["task_id"],
        "root_run_id": root["root_run_id"],
        "work_grant_id": grant["grant_id"],
        "context_pack_id": manifest["context_pack"]["pack_object_id"],
        "source_count": 8,
        "memory_candidate_count": 5,
    }
    request_hash = store._request_hash("project_nexus_selfhost_bootstrap", _semantic_request(manifest))
    return _persist_stage3_complete(store, stage_id, request_hash, final)


def run_project_nexus_selfhost_bootstrap(*, store, authority, budget, trace, runtime, verifier,
                                         memory, context_packs, repo_path: str | Path,
                                         manifest: dict[str, Any]) -> dict[str, Any]:
    """Execute/replay only the explicitly frozen Project Nexus Stage 3 plan."""
    doc = _validate_manifest(manifest, store)
    semantic_request = _semantic_request(doc)
    operation = "project_nexus_selfhost_bootstrap"
    stage_id = doc["stage3_command_id"]
    master_hash = store._request_hash(operation, semantic_request)

    # Completed replay is the first mutable-gate-independent check. A changed
    # manifest conflicts before Git, Authority, Participation, or service work.
    with store._connection() as conn:
        completed = store._replay_command(conn, stage_id, operation, master_hash)
    if completed is not None:
        return completed

    request_id = stage_id + ":request"
    request_operation = "project_nexus_selfhost_request"
    request_hash = store._request_hash(request_operation, semantic_request)
    with store._connection() as conn:
        bound = store._replay_command(conn, request_id, request_operation, request_hash)

    ready_id = stage_id + ":ready-to-close"
    ready_req = _ready_request(doc, master_hash)
    ready_prior = _ledger_replay(store, ready_id, "project_nexus_selfhost_ready_to_close", ready_req) if bound is not None else None
    close_succeeded = doc["root_close"]["succeeded"]
    terminal_req = _transition_request(doc["root"]["root_run_id"], "VERIFYING", "SUCCEEDED",
                                       close_succeeded["classification_assertion"]["assertion_id"])
    terminal_prior = _ledger_replay(store, close_succeeded["command_id"], "transition_run", terminal_req) if bound is not None else None

    # A committed terminal transition is an immutable historical fact. Handle
    # terminal recovery before consulting current participation/runtime state,
    # Git availability, or the Work Grant.
    if bound is not None and (ready_prior is not None or terminal_prior is not None):
        return _finish_terminal(store=store, authority=authority, trace=trace, manifest=doc,
                                master_hash=master_hash, terminal_replay=terminal_prior)

    participation = ParticipationModeService(store)
    runtime_mode = RuntimeModeService(store, authority)
    current_participation = participation.current().get("mode")
    runtime_mode.current()
    _check_instance_binding(store, doc["instance_expectation"])

    # Existing master identity is checked before re-reading Git, so a changed
    # tail cannot use filesystem availability to alter replay semantics.
    if current_participation != NexusParticipationMode.ACTIVE.value:
        _fail("STAGE3_PARTICIPATION_NOT_ACTIVE")
    _preflight(store=store, authority=authority, participation=participation,
               runtime_mode=runtime_mode, repo_path=repo_path, manifest=doc)
    if bound is None:
        # Legacy children without a master commitment cannot be safely adopted.
        known_children = [doc["work_grant"]["create_command_id"], doc["control_grant"]["revoke_command_id"],
                          doc["root"]["create_task_root_command_id"] + "-task"]
        with store._connection() as conn:
            if any(conn.execute("SELECT 1 FROM command_ledger WHERE command_id=?", (item,)).fetchone() for item in known_children):
                _fail("STAGE3_MASTER_REQUEST_BINDING_MISSING")
        store.bind_command_request(command_id=request_id, operation=request_operation, request=semantic_request)

    grant_doc = doc["work_grant"]["grant"]
    now = datetime.now(timezone.utc)
    try:
        if _parse_time(grant_doc["issued_at"]) > now or _parse_time(grant_doc["expires_at"]) <= now:
            _fail("STAGE3_WORK_GRANT_EXPIRED")
    except (KeyError, TypeError, ValueError):
        _fail("STAGE3_WORK_GRANT_UNSAFE")

    # Required order: Work Grant first, then retire the control Grant.
    authority.create_grant(grant_doc, doc["work_grant"]["create_command_id"])
    authority.revoke_grant(doc["control_grant"]["grant_id"], doc["control_grant"]["revoke_command_id"])
    if participation.current().get("mode") != NexusParticipationMode.ACTIVE.value:
        _fail("STAGE3_PARTICIPATION_NOT_ACTIVE")
    try:
        _require_work_grant_current(authority, grant_doc)
    except ProjectNexusSelfHostError:
        raise

    root = doc["root"]
    from adapters.client.hosted import CodexHostedBridge
    hosted_bridge = CodexHostedBridge(store=store, authority=authority, budget=budget,
                                      trace=trace, runtime=runtime, verifier=verifier)
    root_result = hosted_bridge.create_task_root(
        command_id=root["create_task_root_command_id"], created_at=root["created_at"],
        task_id=root["task_id"], requester_id=root["requester_id"], grant_id=root["grant_id"],
        root_run_id=root["root_run_id"], budget_account_id=root["budget_account_id"],
        budget_limits=root["budget_limits"], input_object_id=root["input_object_id"],
        input_payload=root["input_payload"].encode("utf-8"), task_contract=root["task_contract"],
        contract_object_id=root["contract_object_id"], dag_nodes=root["dag_nodes"],
        root_manifest_object_id=root["root_manifest_object_id"], data_boundary=root["data_boundary"],
        classifications=root["classifications"],
    )
    if root_result.get("status") != "RUNNING":
        _fail("STAGE3_ROOT_CREATE_FAILED")

    imported: list[dict[str, Any]] = []
    for source in doc["git_sources"]:
        imported.append(import_git_source(store=store, authority=authority, trace=trace,
                                          repository_path=repo_path, plan=source["import_plan"]))

    grant_id, task_id, run_id = grant_doc["grant_id"], root["task_id"], root["root_run_id"]
    root_actor = grant_doc["granted_to"]
    state = doc["current_state"]
    _put_canonical_object(store=store, authority=authority, grant_id=grant_id, task_id=task_id,
                          run_id=run_id, classification_command_id=state["classify_command_id"],
                          object_command_id=state["put_command_id"], object_id=state["object_id"],
                          payload=_canonical_bytes(state["payload"]), object_type="artifact",
                          classification=state["classification_assertion"], derived_from=state["source_refs"])

    claim_results = []
    for claim in doc["claims"]:
        _put_canonical_object(store=store, authority=authority, grant_id=grant_id, task_id=task_id,
                              run_id=run_id, classification_command_id=claim["classify_command_id"],
                              object_command_id=claim["put_command_id"], object_id=claim["claim_id"],
                              payload=_canonical_bytes(claim["payload"]), object_type="claim",
                              classification=claim["classification_assertion"], derived_from=claim["evidence_refs"])
        claim_results.append(claim)

    verifying = doc["root_close"]["verifying"]
    _record_classification(authority, classification=verifying["classification_assertion"], grant_id=grant_id,
                           task_id=task_id, command_id=verifying["classification_command_id"])
    run_projection = trace.inspect_run_binding(run_id)
    if run_projection["status"] != "RUNNING":
        _fail("STAGE3_ROOT_STATE_INVALID")
    trace.transition_run(command_id=verifying["command_id"], run_id=run_id,
                         expected_state="RUNNING", next_state="VERIFYING",
                         classification_assertion_ref=verifying["classification_assertion"]["assertion_id"])

    verification_results: list[dict[str, Any]] = []
    for plan in doc["verifications"]:
        result = verifier.verify_object_integrity(verification_id=plan["verification_id"],
                                                  target_ref=plan["target_ref"], evidence_refs=plan["evidence_refs"], run_id=run_id)
        if result.get("verifier_kind") != "T1_DETERMINISTIC":
            _fail("STAGE3_VERIFICATION_KIND_MISMATCH")
        verification_results.append(result)

    memory_results: list[dict[str, Any]] = []
    for candidate in doc["memory_candidates"]:
        result = memory.create_candidate(command_id=candidate["command_id"], candidate_id=candidate["candidate_id"],
                                         claim_ref=candidate["claim_ref"], evidence_refs=candidate["evidence_refs"],
                                         owner=candidate["owner"], classification_assertion_ref=candidate["classification_assertion_ref"],
                                         verification_ref=candidate["verification_ref"], review_trigger=candidate["review_trigger"])
        if result.get("truth_state") != "INFERRED" or result.get("status") != "QUARANTINED":
            _fail("STAGE3_MEMORY_TRUTH_POLICY_MISMATCH")
        memory_results.append(result)

    context = doc["context_pack"]
    _record_classification(authority, classification=context["classification_assertion"], grant_id=grant_id,
                           task_id=task_id, command_id=context["classification_command_id"])
    context_status = context_packs.compile(task_id=task_id, run_id=run_id, grant_id=grant_id,
                                           pack_object_id=context["pack_object_id"],
                                           classification_assertion_ref=context["classification_assertion"]["assertion_id"],
                                           command_id=context["command_id"], source_refs=context["source_refs"],
                                           memory_query=None)

    _require_work_grant_current(authority, grant_doc)
    _assert_current_projection(runtime=runtime, store=store, authority=authority, manifest=doc,
                               imported=imported, verifications=verification_results,
                               candidates=memory_results, context_status=context_status)
    succeeded = doc["root_close"]["succeeded"]
    _record_classification(authority, classification=succeeded["classification_assertion"], grant_id=grant_id,
                           task_id=task_id, command_id=succeeded["classification_command_id"])
    ready_request = _ready_request(doc, master_hash)
    _record_ready(store, ready_id, ready_request)

    # READY-TO-CLOSE is the irreversible business-phase boundary. No source,
    # verification, Memory, or Context work is re-entered after this point.
    return _finish_terminal(store=store, authority=authority, trace=trace, manifest=doc,
                            master_hash=master_hash, terminal_replay=None)
