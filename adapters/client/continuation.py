"""HUMAN-authorized, replayable continuation-state commit for one daily Task.

This is an application workflow over the existing ObjectStore, Authority,
Context Pack, and logical-ref APIs. It does not admit Memory or execute work.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Callable

from adapters.client.task_finish import _check_work_closure
from adapters.client.task_start import _ALLOWED_ACTIONS, _ALLOWED_AUDIENCES
from kernel.authority.errors import AuthorizationDenied, InvalidDelegation
from kernel.context.service import _MAX_PACK_BYTES, _SOURCE_ORDER, _canonical
from kernel.object.errors import CommandConflict
from kernel.participation import NexusParticipationMode, ParticipationModeService
from kernel.runtime.errors import RuntimeDenied
from kernel.runtime.modes import RuntimeModeService


PROTOCOL_VERSION = "nexus.continuation_commit@1"
CURRENT_STATE_REF = "project-nexus:current-state"
_LOGICAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_GIT_OID = re.compile(r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
_SAFE_ACTIONS = _ALLOWED_ACTIONS
_MAX_TEXT_BYTES = 8192
_ABSOLUTE_PATH_TEXT = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]|(?<![A-Za-z0-9:/])/(?!/)[^/\\\s]|"
    r"(?<![A-Za-z0-9])\\(?![\\\s])|(?<![A-Za-z0-9:])//[^/\\\s]"
)
_MUTABLE_PRIOR_KEYS = frozenset({
    "as_of", "as_of_execution_code", "accepted_revision", "accepted_git_revision",
    "current_objective", "objective", "current_operating_priority", "next_step",
    "next_product_priority", "recent_work", "completed_task", "completed_task_ref",
    "completed_root_run", "completed_root_run_ref", "work_outcome", "operator_accepted_work_outcome",
    "task_run_lifecycle_at_commit", "governed_work_reference", "outcome",
    "source_refs", "evidence_refs", "context_snapshot_refs", "status",
    "current_state_ref", "state_revision", "previous_current_state", "previous_context", "accepted_git",
    "retained_canonical_facts", "supersedes", "provenance_by_fact",
})


class ContinuationCommitError(Exception):
    """Stable, sanitized operator-facing reason."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


def _fail(reason: str) -> None:
    raise ContinuationCommitError(reason)


def _canonical_json(value: Any) -> bytes:
    try:
        return _canonical(value)
    except Exception:
        _fail("CONTINUATION_PLAN_INVALID")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _pairs_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("CONTINUATION_PLAN_INVALID")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("CONTINUATION_PLAN_INVALID")


def read_continuation_plan(path: str | Path) -> dict[str, Any]:
    """Read process-local strict UTF-8 JSON; the path is never persisted."""
    try:
        value = json.loads(
            Path(path).expanduser().read_bytes().decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs_no_duplicates,
            parse_constant=_reject_constant,
        )
    except Exception:
        raise ContinuationCommitError("CONTINUATION_PLAN_INVALID") from None
    if not isinstance(value, dict):
        _fail("CONTINUATION_PLAN_INVALID")
    return value


def _exact(value: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        _fail("CONTINUATION_PLAN_INVALID")
    return value


def _logical_id(value: Any) -> bool:
    if not isinstance(value, str) or value in {".", ".."} or "*" in value:
        return False
    if _LOGICAL_ID.fullmatch(value) is None:
        return False
    return not Path(value).is_absolute() and not PureWindowsPath(value).is_absolute() and not PureWindowsPath(value).drive


def _require_id(value: Any) -> str:
    if not _logical_id(value):
        _fail("CONTINUATION_PLAN_INVALID")
    return value


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or _TIMESTAMP.fullmatch(value) is None:
        _fail("CONTINUATION_PLAN_INVALID")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except ValueError:
        _fail("CONTINUATION_PLAN_INVALID")
    if parsed.isoformat(timespec="microseconds").replace("+00:00", "Z") != value:
        _fail("CONTINUATION_PLAN_INVALID")
    return parsed


def _text(value: Any, reason: str = "CONTINUATION_PLAN_INVALID") -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(reason)
    try:
        if len(value.encode("utf-8", errors="strict")) > _MAX_TEXT_BYTES:
            _fail(reason)
    except UnicodeError:
        _fail(reason)
    if _ABSOLUTE_PATH_TEXT.search(value):
        _fail(reason)
    return value


def _run_git(repo_path: str | Path, args: list[str]) -> str:
    env = {
        "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_LAZY_FETCH": "1", "GIT_NO_REPLACE_OBJECTS": "1",
    }
    try:
        result = subprocess.run(
            ["git", "-C", str(Path(repo_path).expanduser().resolve()), *args],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="strict", timeout=15, check=False, env={**os.environ, **env},
        )
    except Exception:
        _fail("CONTINUATION_GIT_FACT_UNAVAILABLE")
    if result.returncode != 0:
        _fail("CONTINUATION_GIT_FACT_UNAVAILABLE")
    return result.stdout.strip()


def _validate_git_fact(repo_path: str | Path, git_fact: dict[str, Any]) -> None:
    """Verify local commit, branch, cleanliness, and configured upstream ref.

    The remote expectation is explicitly the local remote-tracking ref; this
    command does not fetch or claim a live remote observation.
    """
    try:
        _run_git(repo_path, ["rev-parse", "--show-toplevel"])
        branch = _run_git(repo_path, ["symbolic-ref", "--quiet", "--short", "HEAD"])
        head = _run_git(repo_path, ["rev-parse", "--verify", "HEAD^{commit}"])
        status = _run_git(repo_path, ["status", "--porcelain=v1", "--untracked-files=all"])
        upstream = _run_git(repo_path, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"])
        upstream_oid = _run_git(repo_path, ["rev-parse", "--verify", "refs/remotes/" + upstream + "^{commit}"])
    except ContinuationCommitError:
        raise
    except Exception:
        _fail("CONTINUATION_GIT_FACT_UNAVAILABLE")
    if (branch != git_fact["branch"] or head != git_fact["commit_sha"]
            or status != "" or git_fact["worktree_state"] != "CLEAN"
            or upstream != git_fact["upstream_ref"] or upstream_oid != git_fact["upstream_commit_sha"]):
        _fail("CONTINUATION_GIT_FACT_MISMATCH")


def _validate_plan_shape(store, authority, plan: dict[str, Any]) -> dict[str, Any]:
    top = _exact(plan, {
        "protocol_version", "command_id", "instance_expectation", "operator_principal_id",
        "task_id", "root_run_id", "grant_id", "expected_previous_state",
        "expected_previous_context", "git_fact", "human_assertions", "as_of",
        "outputs", "stable_sources",
    })
    if top["protocol_version"] != PROTOCOL_VERSION:
        _fail("CONTINUATION_PLAN_INVALID")
    command_id = _require_id(top["command_id"])
    if len(command_id) > 64:
        _fail("CONTINUATION_PLAN_INVALID")
    expected_instance = _exact(top["instance_expectation"], {
        "instance_id", "policy_version", "policy_sha256", "journal_identity",
    })
    for key in ("instance_id", "policy_version"):
        _require_id(expected_instance[key])
    for key in ("policy_sha256", "journal_identity"):
        if not isinstance(expected_instance[key], str) or _SHA256.fullmatch(expected_instance[key]) is None:
            _fail("CONTINUATION_PLAN_INVALID")
    operator_id = _require_id(top["operator_principal_id"])
    task_id = _require_id(top["task_id"])
    run_id = _require_id(top["root_run_id"])
    grant_id = _require_id(top["grant_id"])
    if len({command_id, operator_id, task_id, run_id, grant_id}) != 5:
        _fail("CONTINUATION_PLAN_INVALID")

    prior = _exact(top["expected_previous_state"], {
        "ref_id", "object_id", "integrity_sha256", "current_ref_revision", "accepted_revision",
    })
    if prior["ref_id"] != CURRENT_STATE_REF:
        _fail("CONTINUATION_PLAN_INVALID")
    _require_id(prior["object_id"])
    if not isinstance(prior["integrity_sha256"], str) or _SHA256.fullmatch(prior["integrity_sha256"]) is None:
        _fail("CONTINUATION_PLAN_INVALID")
    revision = prior["current_ref_revision"]
    if revision is not None and (type(revision) is not int or revision < 1):
        _fail("CONTINUATION_PLAN_INVALID")
    if not isinstance(prior["accepted_revision"], str) or _GIT_OID.fullmatch(prior["accepted_revision"]) is None:
        _fail("CONTINUATION_PLAN_INVALID")

    prior_context = _exact(top["expected_previous_context"], {"pack_ref", "content_hash", "integrity_sha256"})
    _require_id(prior_context["pack_ref"])
    for key in ("content_hash", "integrity_sha256"):
        if not isinstance(prior_context[key], str) or _SHA256.fullmatch(prior_context[key]) is None:
            _fail("CONTINUATION_PLAN_INVALID")

    git_fact = _exact(top["git_fact"], {
        "repository_id", "branch", "commit_sha", "worktree_state", "upstream_ref", "upstream_commit_sha",
    })
    _require_id(git_fact["repository_id"])
    if (not isinstance(git_fact["branch"], str) or not git_fact["branch"] or git_fact["branch"].startswith("-")
            or ".." in git_fact["branch"] or "\\" in git_fact["branch"] or git_fact["branch"].startswith("/")):
        _fail("CONTINUATION_PLAN_INVALID")
    for key in ("commit_sha", "upstream_commit_sha"):
        if not isinstance(git_fact[key], str) or _GIT_OID.fullmatch(git_fact[key]) is None:
            _fail("CONTINUATION_PLAN_INVALID")
    if git_fact["worktree_state"] != "CLEAN":
        _fail("CONTINUATION_PLAN_INVALID")
    upstream_ref = git_fact["upstream_ref"]
    if (not isinstance(upstream_ref, str) or not upstream_ref or upstream_ref.startswith("-")
            or ":" in upstream_ref or "\\" in upstream_ref or upstream_ref.startswith("/")):
        _fail("CONTINUATION_PLAN_INVALID")
    assertions = _exact(top["human_assertions"], {"current_objective", "next_step", "recent_work"})
    assertions = {key: _text(assertions[key], "CONTINUATION_HUMAN_ASSERTION_INVALID") for key in assertions}
    as_of = _timestamp(top["as_of"])
    if as_of > datetime.now(timezone.utc):
        _fail("CONTINUATION_TIMESTAMP_NOT_CURRENT")

    outputs_in = _exact(top["outputs"], {
        "current_state_object_id", "what_changed_object_id", "context_pack_object_id",
        "current_state_classification", "what_changed_classification", "context_pack_classification",
    })
    output_ids = {key: _require_id(outputs_in[key]) for key in (
        "current_state_object_id", "what_changed_object_id", "context_pack_object_id",
    )}
    if len(set(output_ids.values())) != 3:
        _fail("CONTINUATION_PLAN_INVALID")
    classes = {}
    for key in ("current_state_classification", "what_changed_classification", "context_pack_classification"):
        item = _exact(outputs_in[key], {"assertion_id", "sensitivity_level", "handling_tags"})
        assertion_id = _require_id(item["assertion_id"])
        level = item["sensitivity_level"]
        tags = item["handling_tags"]
        if (not isinstance(level, str) or not isinstance(tags, list)
                or any(not isinstance(tag, str) or not tag for tag in tags)
                or len(tags) != len(set(tags))):
            _fail("CONTINUATION_CLASSIFICATION_INVALID")
        classes[key] = {"assertion_id": assertion_id, "sensitivity_level": level, "handling_tags": list(tags)}
    class_ids = [item["assertion_id"] for item in classes.values()]
    if len(class_ids) != len(set(class_ids)):
        _fail("CONTINUATION_CLASSIFICATION_INVALID")

    stable = top["stable_sources"]
    if not isinstance(stable, list) or not stable or len(stable) > 48:
        _fail("CONTINUATION_CONTEXT_SOURCE_INVALID")
    stable_items = []
    for item in stable:
        item = _exact(item, {"source_ref", "snapshot_object_id", "classification_assertion_id"})
        stable_items.append({
            "source_ref": _require_id(item["source_ref"]),
            "snapshot_object_id": _require_id(item["snapshot_object_id"]),
            "classification_assertion_id": _require_id(item["classification_assertion_id"]),
        })
    refs = [item["source_ref"] for item in stable_items]
    snapshot_ids = [item["snapshot_object_id"] for item in stable_items]
    snapshot_class_ids = [item["classification_assertion_id"] for item in stable_items]
    if (len(refs) != len(set(refs)) or len(snapshot_ids) != len(set(snapshot_ids))
            or len(snapshot_class_ids) != len(set(snapshot_class_ids))
            or refs != sorted(refs, key=lambda value: value.encode("utf-8"))):
        _fail("CONTINUATION_CONTEXT_SOURCE_INVALID")
    all_ids = [*output_ids.values(), *snapshot_ids, *class_ids, *snapshot_class_ids]
    if len(all_ids) != len(set(all_ids)):
        _fail("CONTINUATION_PLAN_INVALID")

    command_ids = _command_closure(command_id, stable_items)
    if len(command_ids) != len(set(command_ids)) or any(len(value) > 128 for value in command_ids):
        _fail("CONTINUATION_COMMAND_ID_INVALID")
    return {
        "command_id": command_id, "request_command_id": command_id + ":request",
        "instance_expectation": dict(expected_instance), "operator_id": operator_id,
        "task_id": task_id, "run_id": run_id, "grant_id": grant_id,
        "prior": dict(prior), "prior_context": dict(prior_context), "git_fact": dict(git_fact),
        "human_assertions": assertions, "as_of": top["as_of"], "as_of_dt": as_of,
        "output_ids": output_ids, "classes": classes, "stable_sources": stable_items,
        "command_ids": command_ids,
    }


def _command_closure(command_id: str, stable_sources: list[dict[str, str]]) -> list[str]:
    commands = [
        command_id + ":request", command_id + ":class-current-state", command_id + ":put-current-state",
        command_id + ":class-what-changed", command_id + ":put-what-changed",
        command_id + ":supersedes", command_id + ":current-ref-init", command_id + ":current-ref-cas",
        command_id + ":class-context-pack", command_id + ":class-current-state-authorize",
        command_id + ":class-what-changed-authorize", command_id + ":class-context-pack-authorize",
        command_id + ":put-current-state:authorize", command_id + ":put-what-changed:authorize",
        command_id + ":supersedes-authorize", command_id + ":current-ref-authorize",
        command_id + ":context", command_id + ":context-object", command_id + ":context-authorize-pack",
    ]
    for index, _item in enumerate(stable_sources):
        prefix = f"{command_id}:snapshot:{index:02d}"
        commands.extend((prefix + ":class", prefix + ":class-authorize", prefix + ":put", prefix + ":put:authorize"))
    return commands


def _read_current_ref(store) -> dict[str, Any] | None:
    with store._connection() as conn:
        row = conn.execute(
            "SELECT ref_id,ref_type,current_object_id,revision FROM logical_refs WHERE ref_id=?",
            (CURRENT_STATE_REF,),
        ).fetchone()
    return dict(row) if row else None


def _read_projection(store, normalized: dict[str, Any], *, allow_historical: bool = False) -> dict[str, Any]:
    with store._connection() as conn:
        task = conn.execute(
            "SELECT task_id,requester_id,status,root_run_id FROM tasks WHERE task_id=?",
            (normalized["task_id"],),
        ).fetchone()
        run = conn.execute(
            "SELECT run_id,task_id,parent_run_id,executor_kind,status,grant_id,data_boundary_json "
            "FROM runs WHERE run_id=?", (normalized["run_id"],),
        ).fetchone()
        grant = conn.execute("SELECT * FROM delegation_grants WHERE grant_id=?", (normalized["grant_id"],)).fetchone()
        operator = conn.execute("SELECT principal_type,status FROM principals WHERE principal_id=?", (normalized["operator_id"],)).fetchone()
        runtime = conn.execute("SELECT principal_type,status FROM principals WHERE principal_id=?", (grant["granted_to"],)).fetchone() if grant else None
        anchor = conn.execute("SELECT policy_ref FROM trust_anchors WHERE principal_id=?", (normalized["operator_id"],)).fetchone()
    if not task or not run or not grant:
        _fail("CONTINUATION_TASK_BINDING_MISMATCH")
    if (task["task_id"] != normalized["task_id"] or task["requester_id"] != normalized["operator_id"]
            or task["root_run_id"] != normalized["run_id"]
            or run["run_id"] != normalized["run_id"] or run["task_id"] != normalized["task_id"]
            or run["parent_run_id"] is not None or run["executor_kind"] != "ORCHESTRATOR"
            or run["grant_id"] != normalized["grant_id"]
            or (not allow_historical and (task["status"] != "ACTIVE" or run["status"] != "RUNNING"))):
        _fail("CONTINUATION_TASK_NOT_ACTIVE")
    if not allow_historical:
        if (not operator or tuple(operator) != ("HUMAN", "ACTIVE")
                or normalized["operator_id"] not in store.policy.get("trust_anchors", [])
                or not anchor or anchor["policy_ref"] != store.policy["policy_version"]):
            _fail("CONTINUATION_OPERATOR_UNTRUSTED")
        if not runtime or tuple(runtime) != ("SERVICE", "ACTIVE"):
            _fail("CONTINUATION_RUNTIME_PRINCIPAL_INVALID")
    try:
        boundary = json.loads(run["data_boundary_json"])
        scope = {
            "task_scope": json.loads(grant["task_scope_json"]),
            "resource_scope": json.loads(grant["resource_scope_json"]),
            "action_scope": json.loads(grant["action_scope_json"]),
            "audience_scope": json.loads(grant["audience_scope_json"]),
        }
        store._validate("nexus.delegation_grant@1.schema.json", {
            "schema_id": "nexus.delegation_grant", "schema_version": 1,
            "grant_id": grant["grant_id"], "issued_by": grant["issued_by"],
            "granted_to": grant["granted_to"], **scope,
            "issued_at": grant["issued_at"], "expires_at": grant["expires_at"],
            "status": grant["status"], "policy_version": grant["policy_version"],
            **({"credential_ref": grant["credential_ref"]} if grant["credential_ref"] is not None else {}),
            **({"parent_grant_id": grant["parent_grant_id"]} if grant["parent_grant_id"] is not None else {}),
        })
    except Exception:
        _fail("CONTINUATION_AUTHORITY_INVALID")
    if (not isinstance(boundary, dict) or not isinstance(boundary.get("allowed_classifications"), list)
            or not isinstance(boundary.get("handling_tags"), list)):
        _fail("CONTINUATION_TASK_BOUNDARY_INVALID")
    return {"task": dict(task), "run": dict(run), "grant": dict(grant), "boundary": boundary,
            "scope": scope, "runtime_principal_id": grant["granted_to"]}


def _validate_modes(store) -> None:
    if RuntimeModeService(store, None).current().get("mode") != "NORMAL":
        _fail("CONTINUATION_RUNTIME_MODE_UNSUPPORTED")
    if ParticipationModeService(store).current().get("mode") != NexusParticipationMode.ACTIVE.value:
        _fail("CONTINUATION_PARTICIPATION_INACTIVE")


def _validate_binding(store, expected: dict[str, Any]) -> None:
    actual = store.get_instance_binding_status()
    if (actual.get("state") not in {"FRESH_BOUND_INSTANCE", "LEGACY_ADOPTED_BOUND_INSTANCE"}
            or actual.get("policy_content_binding") != "BOUND"
            or any(actual.get(key) != expected[key] for key in (
                "instance_id", "policy_version", "policy_sha256", "journal_identity"))):
        _fail("CONTINUATION_INSTANCE_BINDING_MISMATCH")


def _grant_chain(authority, normalized: dict[str, Any], projection: dict[str, Any], *, require_current: bool) -> None:
    grant = projection["grant"]
    scope = projection["scope"]
    if (grant["parent_grant_id"] is not None or grant["issued_by"] != normalized["operator_id"]
            or grant["policy_version"] != authority.policy["policy_version"]
            or scope["task_scope"] != [normalized["task_id"]]):
        _fail("CONTINUATION_AUTHORITY_INVALID")
    if grant["status"] != "ACTIVE":
        _fail("CONTINUATION_AUTHORITY_UNAVAILABLE")
    try:
        expires = datetime.fromisoformat(grant["expires_at"].replace("Z", "+00:00"))
        issued = datetime.fromisoformat(grant["issued_at"].replace("Z", "+00:00"))
    except Exception:
        _fail("CONTINUATION_AUTHORITY_INVALID")
    now = datetime.now(timezone.utc)
    if issued >= expires:
        _fail("CONTINUATION_AUTHORITY_INVALID")
    if require_current and not issued <= now < expires:
        _fail("CONTINUATION_AUTHORITY_UNAVAILABLE")
    if any(not _logical_id(item) for items in scope.values() for item in items):
        _fail("CONTINUATION_AUTHORITY_INVALID")
    for key, items in scope.items():
        if (len(items) != len(set(items))
                or items != sorted(items, key=lambda value: value.encode("utf-8"))):
            _fail("CONTINUATION_AUTHORITY_INVALID")
    actions = set(scope["action_scope"])
    if (any(action not in _SAFE_ACTIONS for action in actions)
            or not {"OBJECT_WRITE", "CLASSIFY", "INSPECT"}.issubset(actions)
            or "nexus-runtime" not in scope["audience_scope"]
            or "nexus-inspect" not in scope["audience_scope"]
            or any(audience not in _ALLOWED_AUDIENCES for audience in scope["audience_scope"])):
        _fail("CONTINUATION_AUTHORITY_SCOPE_INSUFFICIENT")
    try:
        chain = authority.validate_delegation_chain(normalized["grant_id"])
    except (InvalidDelegation, AuthorizationDenied):
        _fail("CONTINUATION_AUTHORITY_UNAVAILABLE")
    if (not chain or chain[-1]["grant_id"] != normalized["grant_id"]
            or chain[-1]["parent_grant_id"] is not None
            or chain[-1]["issued_by"] != normalized["operator_id"]
            or chain[-1]["granted_to"] != grant["granted_to"]):
        _fail("CONTINUATION_AUTHORITY_UNAVAILABLE")


def _canonical_prior_revision(document: dict[str, Any]) -> str:
    value = document.get("accepted_git_revision", document.get("accepted_revision", document.get("as_of_execution_code")))
    if isinstance(value, dict):
        value = value.get("value")
    if not isinstance(value, str) or _GIT_OID.fullmatch(value) is None:
        _fail("CONTINUATION_PREVIOUS_STATE_INVALID")
    return value


def _prior_fact(document: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        if key in document:
            value = document[key]
            if isinstance(value, dict):
                value = value.get("value")
            if isinstance(value, str) and value.strip():
                return value
    _fail("CONTINUATION_PREVIOUS_STATE_INVALID")


def _pack_entry(pack: dict[str, Any], ref: str) -> dict[str, Any] | None:
    found = [entry for entry in pack.get("entries", []) if entry.get("source_ref") == ref]
    return found[0] if len(found) == 1 else None


_SNAPSHOT_ENTRY_SCHEMA = "nexus.continuation_snapshot_entry"
_SNAPSHOT_ENTRY_KEYS = {
    "schema_id", "schema_version", "source_ref", "source_type",
    "classification_assertion_ref", "source_integrity_sha256", "content",
}
_SNAPSHOT_ENTRY_KEYS_WITH_ID = _SNAPSHOT_ENTRY_KEYS | {"snapshot_object_id"}
_STABLE_SOURCE_TYPES = {"artifact", "evidence", "claim", "verification"}


def _decode_snapshot_entry(content: str, *, expected_snapshot_id: str) -> dict[str, Any] | None:
    """Return the original source payload represented by a verified snapshot wrapper.

    A Context Pack read has already verified the immediate snapshot object's bytes.
    Here every wrapper layer is additionally required to be canonical and to bind
    the next embedded content bytes to its declared source integrity hash.
    """
    try:
        current_bytes = content.encode("utf-8", errors="strict")
    except Exception:
        _fail("CONTINUATION_CONTEXT_SOURCE_INVALID")
    try:
        current = json.loads(current_bytes.decode("utf-8"), object_pairs_hook=_pairs_no_duplicates,
                             parse_constant=_reject_constant)
    except Exception:
        return None
    if not isinstance(current, dict) or current.get("schema_id") != _SNAPSHOT_ENTRY_SCHEMA:
        return None

    expected_id = expected_snapshot_id
    while True:
        current_keys = set(current)
        if (current_keys != _SNAPSHOT_ENTRY_KEYS and current_keys != _SNAPSHOT_ENTRY_KEYS_WITH_ID
                or current.get("schema_id") != _SNAPSHOT_ENTRY_SCHEMA
                or type(current.get("schema_version")) is not int or current["schema_version"] != 1
                or not _logical_id(current.get("source_ref"))
                or current.get("source_type") not in _STABLE_SOURCE_TYPES
                or not _logical_id(current.get("classification_assertion_ref"))
                or not isinstance(current.get("source_integrity_sha256"), str)
                or _SHA256.fullmatch(current["source_integrity_sha256"]) is None
                or not isinstance(current.get("content"), str)):
            _fail("CONTINUATION_SNAPSHOT_ENTRY_INVALID")
        if "snapshot_object_id" in current and (
                not _logical_id(current["snapshot_object_id"])
                or (expected_id is not None and current["snapshot_object_id"] != expected_id)):
            _fail("CONTINUATION_SNAPSHOT_ENTRY_INVALID")
        if _canonical_json(current) != current_bytes:
            _fail("CONTINUATION_SNAPSHOT_ENTRY_INVALID")
        try:
            embedded_bytes = current["content"].encode("utf-8", errors="strict")
        except Exception:
            _fail("CONTINUATION_SNAPSHOT_ENTRY_INVALID")
        if _sha256(embedded_bytes) != current["source_integrity_sha256"]:
            _fail("CONTINUATION_SNAPSHOT_ENTRY_INTEGRITY_INVALID")
        try:
            nested = json.loads(embedded_bytes.decode("utf-8"), object_pairs_hook=_pairs_no_duplicates,
                                parse_constant=_reject_constant)
        except Exception:
            nested = None
        if not isinstance(nested, dict) or nested.get("schema_id") != _SNAPSHOT_ENTRY_SCHEMA:
            return current
        expected_id = current["source_ref"]
        current_bytes = embedded_bytes
        current = nested


def _request_binding_exists(store, normalized: dict[str, Any]) -> bool:
    """A ledger row only selects relaxed read validation; its digest is still checked later."""
    with store._connection() as conn:
        return conn.execute("SELECT 1 FROM command_ledger WHERE command_id=?",
                            (normalized["request_command_id"],)).fetchone() is not None


def _prior_context(context_packs, normalized: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    try:
        pack = context_packs.read_compiled(normalized["prior_context"]["pack_ref"])
    except Exception:
        _fail("CONTINUATION_PREVIOUS_CONTEXT_UNAVAILABLE")
    if (pack.get("content_hash") != normalized["prior_context"]["content_hash"]
            or pack.get("integrity_hash") != normalized["prior_context"]["integrity_sha256"]
            or pack.get("model_visible_exposure") != "UNKNOWN"):
        _fail("CONTINUATION_PREVIOUS_CONTEXT_MISMATCH")
    state_id = normalized["prior"]["object_id"]
    state_entry = _pack_entry(pack, state_id)
    if (state_entry is None or state_entry.get("source_type") != "artifact"
            or state_entry.get("source_integrity_sha256") != normalized["prior"]["integrity_sha256"]):
        _fail("CONTINUATION_PREVIOUS_STATE_NOT_IN_CONTEXT")
    try:
        prior_doc = json.loads(state_entry["content"], object_pairs_hook=_pairs_no_duplicates, parse_constant=_reject_constant)
    except Exception:
        _fail("CONTINUATION_PREVIOUS_STATE_INVALID")
    if not isinstance(prior_doc, dict):
        _fail("CONTINUATION_PREVIOUS_STATE_INVALID")
    if _canonical_json(prior_doc) != state_entry["content"].encode("utf-8"):
        _fail("CONTINUATION_PREVIOUS_STATE_INVALID")
    if (prior_doc.get("project_identity", "project-nexus") != "project-nexus"
            or prior_doc.get("current_state_ref", CURRENT_STATE_REF) != CURRENT_STATE_REF
            or (prior_doc.get("schema_id") == "nexus.continuation_state"
                and prior_doc.get("schema_version") != 1)):
        _fail("CONTINUATION_PREVIOUS_STATE_INVALID")
    if _canonical_prior_revision(prior_doc) != normalized["prior"]["accepted_revision"]:
        _fail("CONTINUATION_PREVIOUS_STATE_CONFLICT")
    return pack, state_entry, prior_doc


def _assert_boundary(document: dict[str, Any], boundary: dict[str, Any]) -> None:
    ranks = document.get("sensitivity_level")
    # The actual classification fields are carried alongside source metadata;
    # this helper is only used after those values have been normalized.
    if ranks not in set(boundary["allowed_classifications"]):
        _fail("CONTINUATION_SOURCE_OUTSIDE_TASK_BOUNDARY")
    if not set(document.get("handling_tags", [])).issubset(set(boundary["handling_tags"])):
        _fail("CONTINUATION_SOURCE_OUTSIDE_TASK_BOUNDARY")


def _source_classification(store, object_id: str, assertion_id: str, boundary: dict[str, Any]) -> dict[str, Any]:
    metadata = store.get_object_metadata(object_id)
    if (metadata.get("object_id") != object_id or metadata.get("object_type") not in {"artifact", "evidence", "claim", "verification"}
            or (metadata.get("payload_state"), metadata.get("lifecycle"), metadata.get("validity")) != ("AVAILABLE", "ACTIVE", "VALID")
            or metadata.get("classification_assertion_ref") != assertion_id):
        _fail("CONTINUATION_SOURCE_UNAVAILABLE")
    with store._connection() as conn:
        row = conn.execute("SELECT * FROM classification_assertions WHERE assertion_id=?", (assertion_id,)).fetchone()
    if not row or row["subject_type"] != "OBJECT" or row["subject_ref"] != object_id:
        _fail("CONTINUATION_SOURCE_CLASSIFICATION_INVALID")
    item = {"sensitivity_level": row["sensitivity_level"], "handling_tags": json.loads(row["handling_tags_json"])}
    _assert_boundary(item, boundary)
    return {"metadata": metadata, "classification": item}


def _resource_closure(normalized: dict[str, Any], prior_state_id: str) -> set[str]:
    ids = normalized["output_ids"]
    snapshots = [item["snapshot_object_id"] for item in normalized["stable_sources"]]
    resources = set(ids.values()) | set(snapshots)
    resources.add("logical-ref:" + CURRENT_STATE_REF)
    resources.add(f"relation:{ids['current_state_object_id']}:supersedes:{prior_state_id}")
    resources.update("object:" + item for item in [ids["current_state_object_id"], ids["what_changed_object_id"], *snapshots])
    return resources


def _validate_scope(projection: dict[str, Any], normalized: dict[str, Any], prior_state_id: str) -> None:
    scope = projection["scope"]
    required = _resource_closure(normalized, prior_state_id)
    if not required.issubset(set(scope["resource_scope"])):
        _fail("CONTINUATION_AUTHORITY_SCOPE_INSUFFICIENT")
    if len(scope["resource_scope"]) != len(set(scope["resource_scope"])):
        _fail("CONTINUATION_AUTHORITY_INVALID")
    if any("*" in resource or not _logical_id(resource) for resource in scope["resource_scope"]):
        _fail("CONTINUATION_AUTHORITY_INVALID")
    runtime_resources = {
        *normalized["output_ids"].values(),
        *(item["snapshot_object_id"] for item in normalized["stable_sources"]),
        "logical-ref:" + CURRENT_STATE_REF,
        f"relation:{normalized['output_ids']['current_state_object_id']}:supersedes:{prior_state_id}",
    }
    inspect_resources = {
        "object:" + normalized["output_ids"]["current_state_object_id"],
        "object:" + normalized["output_ids"]["what_changed_object_id"],
        *("object:" + item["snapshot_object_id"] for item in normalized["stable_sources"]),
    }
    if (not runtime_resources.issubset(set(scope["resource_scope"]))
            or not inspect_resources.issubset(set(scope["resource_scope"]))
            or not {"OBJECT_WRITE", "CLASSIFY"}.issubset(set(scope["action_scope"]))
            or not {"nexus-runtime", "nexus-inspect"}.issubset(set(scope["audience_scope"]))):
        _fail("CONTINUATION_AUTHORITY_SCOPE_INSUFFICIENT")


def _read_request(store, normalized: dict[str, Any], request: dict[str, Any]) -> bool:
    digest = store._request_hash("continuation_commit_request", request)
    try:
        with store._connection() as conn:
            prior = store._replay_command(conn, normalized["request_command_id"], "continuation_commit_request", digest)
    except CommandConflict:
        _fail("COMMAND_CONFLICT")
    if prior is not None and prior != {"status": "REQUEST_BOUND"}:
        _fail("CONTINUATION_REQUEST_BINDING_INVALID")
    return prior is not None


def _planned_classification(normalized: dict[str, Any], class_key: str, subject_ref: str,
                            runtime_id: str, policy_version: str) -> dict[str, Any]:
    item = normalized["classes"][class_key]
    return {
        "schema_id": "nexus.classification_assertion", "schema_version": 1,
        "assertion_id": item["assertion_id"], "subject_type": "OBJECT", "subject_ref": subject_ref,
        "sensitivity_level": item["sensitivity_level"], "handling_tags": list(item["handling_tags"]),
        "policy_version": policy_version, "reason": "HUMAN-authorized continuation commit.",
        "actor_id": runtime_id,
    }


def _snapshot_classification(item: dict[str, Any], runtime_id: str, policy_version: str,
                             source_class: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_id": "nexus.classification_assertion", "schema_version": 1,
        "assertion_id": item["classification_assertion_id"], "subject_type": "OBJECT",
        "subject_ref": item["snapshot_object_id"],
        "sensitivity_level": source_class["sensitivity_level"],
        "handling_tags": list(source_class["handling_tags"]), "policy_version": policy_version,
        "reason": "Bounded source snapshot from a verified prior Context Pack.", "actor_id": runtime_id,
    }


def _retained_facts(prior_doc: dict[str, Any]) -> dict[str, Any]:
    return {key: prior_doc[key] for key in sorted(prior_doc) if key not in _MUTABLE_PRIOR_KEYS}


def _superseded_fact_keys(prior_doc: dict[str, Any], prior_state_id: str) -> list[str]:
    replaceable = {
        "as_of", "as_of_execution_code", "accepted_revision", "accepted_git_revision",
        "current_objective", "objective", "current_operating_priority", "next_step",
        "next_product_priority", "recent_work", "completed_task", "completed_task_ref",
        "completed_root_run", "completed_root_run_ref", "work_outcome", "outcome", "status",
        "source_refs", "evidence_refs", "context_snapshot_refs", "previous_context",
    }
    return sorted({key for key in prior_doc if key in replaceable} | {prior_state_id},
                  key=lambda value: value.encode("utf-8"))


def _build_documents(store, normalized: dict[str, Any], projection: dict[str, Any],
                     prior_state_entry: dict[str, Any], prior_doc: dict[str, Any],
                     prior_pack: dict[str, Any]) -> dict[str, Any]:
    prior_state_id = normalized["prior"]["object_id"]
    current_id = normalized["output_ids"]["current_state_object_id"]
    delta_id = normalized["output_ids"]["what_changed_object_id"]
    pack_id = normalized["output_ids"]["context_pack_object_id"]
    expected_rev = normalized["prior"]["current_ref_revision"] or 0
    next_ref_revision = expected_rev + 1 if expected_rev else 2
    previous_objective = _prior_fact(prior_doc, ("current_objective", "objective"))
    previous_next = _prior_fact(prior_doc, ("current_operating_priority", "next_step", "next_product_priority"))
    previous_revision = normalized["prior"]["accepted_revision"]
    human = normalized["human_assertions"]
    git = normalized["git_fact"]

    snapshot_entries = []
    snapshot_payloads: dict[str, bytes] = {}
    snapshot_classes: dict[str, dict[str, Any]] = {}
    original_entries = {}
    prior_meta = _source_classification(store, normalized["prior"]["object_id"],
                                        prior_state_entry["classification_assertion_ref"], projection["boundary"])
    for item in normalized["stable_sources"]:
        entry = _pack_entry(prior_pack, item["source_ref"])
        if entry is None or entry.get("source_type") not in _STABLE_SOURCE_TYPES:
            _fail("CONTINUATION_CONTEXT_SOURCE_INVALID")
        try:
            source_bytes = entry["content"].encode("utf-8", errors="strict")
        except Exception:
            _fail("CONTINUATION_CONTEXT_SOURCE_INVALID")
        if _sha256(source_bytes) != entry["source_integrity_sha256"]:
            _fail("CONTINUATION_CONTEXT_SOURCE_INTEGRITY_INVALID")
        source_meta = _source_classification(store, item["source_ref"], entry["classification_assertion_ref"], projection["boundary"])
        if source_meta["metadata"]["integrity_hash"] != entry["source_integrity_sha256"]:
            _fail("CONTINUATION_CONTEXT_SOURCE_INTEGRITY_INVALID")
        if source_meta["metadata"].get("object_type") != entry["source_type"]:
            _fail("CONTINUATION_CONTEXT_SOURCE_INVALID")
        origin = _decode_snapshot_entry(entry["content"], expected_snapshot_id=item["source_ref"])
        if origin is None:
            origin = {
                "source_ref": item["source_ref"], "source_type": entry["source_type"],
                "classification_assertion_ref": entry["classification_assertion_ref"],
                "source_integrity_sha256": entry["source_integrity_sha256"],
                "content": entry["content"],
            }
        classification = _snapshot_classification(item, projection["runtime_principal_id"],
                                                  store.policy["policy_version"], source_meta["classification"])
        try:
            store._validate("nexus.classification_assertion@1.schema.json", classification)
        except Exception:
            _fail("CONTINUATION_CLASSIFICATION_INVALID")
        snapshot_doc = {
            "schema_id": "nexus.continuation_snapshot_entry", "schema_version": 1,
            "snapshot_object_id": item["snapshot_object_id"],
            "source_ref": origin["source_ref"], "source_type": origin["source_type"],
            "classification_assertion_ref": origin["classification_assertion_ref"],
            "source_integrity_sha256": origin["source_integrity_sha256"],
            "content": origin["content"],
        }
        snapshot_bytes = _canonical_json(snapshot_doc)
        snapshot_entries.append({
            "source_ref": item["snapshot_object_id"], "source_type": "artifact",
            "classification_assertion_ref": item["classification_assertion_id"],
            "source_integrity_sha256": _sha256(snapshot_bytes), "content": snapshot_bytes.decode("utf-8"),
        })
        snapshot_payloads[item["snapshot_object_id"]] = snapshot_bytes
        snapshot_classes[item["snapshot_object_id"]] = classification
        original_entries[item["source_ref"]] = entry

    current_class = _planned_classification(normalized, "current_state_classification", current_id,
                                            projection["runtime_principal_id"], store.policy["policy_version"])
    delta_class = _planned_classification(normalized, "what_changed_classification", delta_id,
                                          projection["runtime_principal_id"], store.policy["policy_version"])
    pack_class = _planned_classification(normalized, "context_pack_classification", pack_id,
                                         projection["runtime_principal_id"], store.policy["policy_version"])
    for classification in (current_class, delta_class, pack_class):
        try:
            store._validate("nexus.classification_assertion@1.schema.json", classification)
        except Exception:
            _fail("CONTINUATION_CLASSIFICATION_INVALID")
        _assert_boundary({"sensitivity_level": classification["sensitivity_level"],
                          "handling_tags": classification["handling_tags"]}, projection["boundary"])

    ranks = store.policy["classification"]["sensitivity_rank"]
    lineage_classes = [prior_meta["classification"], *snapshot_classes.values()]
    for classification in (current_class, delta_class):
        if any(ranks[classification["sensitivity_level"]] < ranks[item["sensitivity_level"]]
               or not set(item["handling_tags"]).issubset(set(classification["handling_tags"]))
               for item in lineage_classes):
            _fail("CONTINUATION_CLASSIFICATION_DOWNGRADE")
    if any(ranks[pack_class["sensitivity_level"]] < ranks[item["sensitivity_level"]]
           or not set(item["handling_tags"]).issubset(set(pack_class["handling_tags"]))
           for item in (current_class, delta_class, *snapshot_classes.values())):
        _fail("CONTINUATION_CLASSIFICATION_DOWNGRADE")

    retained = _retained_facts(prior_doc)
    source_refs = sorted({prior_state_id, normalized["prior_context"]["pack_ref"],
                          *(item["source_ref"] for item in normalized["stable_sources"])},
                         key=lambda value: value.encode("utf-8"))
    snapshot_refs = [item["snapshot_object_id"] for item in normalized["stable_sources"]]
    state_doc = {
        "schema_id": "nexus.continuation_state", "schema_version": 1,
        "project_identity": "project-nexus", "current_state_ref": CURRENT_STATE_REF,
        "state_revision": next_ref_revision, "as_of": normalized["as_of"],
        "previous_current_state": {
            "ref_id": CURRENT_STATE_REF, "object_id": prior_state_id,
            "integrity_sha256": normalized["prior"]["integrity_sha256"],
            "logical_ref_revision": expected_rev,
        },
        "previous_context": {
            "pack_ref": normalized["prior_context"]["pack_ref"],
            "content_hash": normalized["prior_context"]["content_hash"],
            "integrity_sha256": normalized["prior_context"]["integrity_sha256"],
        },
        "accepted_git": {
            "repository_id": git["repository_id"], "branch": git["branch"],
            "commit_sha": git["commit_sha"], "worktree_state": git["worktree_state"],
            "upstream_ref": git["upstream_ref"], "upstream_commit_sha": git["upstream_commit_sha"],
        },
        "accepted_revision": git["commit_sha"],
        "current_objective": human["current_objective"],
        "current_operating_priority": human["next_step"],
        "recent_work": [{
            "summary": human["recent_work"], "task_id": normalized["task_id"],
            "root_run_id": normalized["run_id"], "outcome": "SUCCEEDED",
        }],
        "operator_accepted_work_outcome": {
            "value": "SUCCEEDED", "task_id": normalized["task_id"],
            "root_run_id": normalized["run_id"],
            "asserted_by": normalized["operator_id"],
        },
        "task_run_lifecycle_at_commit": {
            "task_id": normalized["task_id"], "root_run_id": normalized["run_id"],
            "task_status": "ACTIVE", "root_run_status": "RUNNING",
        },
        "governed_work_reference": {
            "task_id": normalized["task_id"], "root_run_id": normalized["run_id"],
        },
        "retained_canonical_facts": retained,
        "supersedes": {"ref_id": CURRENT_STATE_REF, "object_id": prior_state_id,
                       "integrity_sha256": normalized["prior"]["integrity_sha256"]},
        "source_refs": source_refs,
        "context_snapshot_refs": snapshot_refs,
        "provenance_by_fact": {
            "accepted_revision": [
                {"kind": "GIT_FACT", "branch": git["branch"],
                 "commit_sha": git["commit_sha"], "upstream_ref": git["upstream_ref"],
                 "upstream_commit_sha": git["upstream_commit_sha"], "worktree_state": "CLEAN"},
                {"kind": "HUMAN_OPERATOR_ASSERTION", "operator_principal_id": normalized["operator_id"],
                 "repository_id": git["repository_id"], "task_id": normalized["task_id"],
                 "root_run_id": normalized["run_id"]},
            ],
            "current_objective": [{"kind": "HUMAN_OPERATOR_ASSERTION", "operator_principal_id": normalized["operator_id"],
                                   "task_id": normalized["task_id"], "root_run_id": normalized["run_id"]}],
            "current_operating_priority": [{"kind": "HUMAN_OPERATOR_ASSERTION", "operator_principal_id": normalized["operator_id"],
                                             "task_id": normalized["task_id"], "root_run_id": normalized["run_id"]}],
            "recent_work": [{"kind": "HUMAN_OPERATOR_ASSERTION", "operator_principal_id": normalized["operator_id"],
                              "task_id": normalized["task_id"], "root_run_id": normalized["run_id"]}],
            "operator_accepted_work_outcome": [{"kind": "HUMAN_OPERATOR_ASSERTION",
                                                 "operator_principal_id": normalized["operator_id"],
                                                 "task_id": normalized["task_id"],
                                                 "root_run_id": normalized["run_id"]}],
            "task_run_lifecycle_at_commit": [{"kind": "NEXUS_LIFECYCLE_FACT",
                                               "task_id": normalized["task_id"],
                                               "root_run_id": normalized["run_id"],
                                               "task_status": "ACTIVE", "run_status": "RUNNING"}],
            "governed_work_reference": [{"kind": "NEXUS_LIFECYCLE_FACT",
                                          "task_id": normalized["task_id"],
                                          "root_run_id": normalized["run_id"]}],
            "retained_canonical_facts": [{"kind": "EXISTING_CANONICAL_FACT", "object_id": prior_state_id,
                                          "integrity_sha256": normalized["prior"]["integrity_sha256"],
                                          "context_pack_ref": normalized["prior_context"]["pack_ref"]}],
        },
    }
    state_payload = _canonical_json(state_doc)
    if _sha256(state_payload) == normalized["prior"]["integrity_sha256"]:
        _fail("CONTINUATION_STATE_CONTENT_UNCHANGED")

    previous_objective = previous_objective
    previous_next = previous_next
    changed = {
        "schema_id": "nexus.what_changed", "schema_version": 1,
        "previous_state": {"ref_id": CURRENT_STATE_REF, "object_id": prior_state_id,
                           "integrity_sha256": normalized["prior"]["integrity_sha256"],
                           "context_pack_ref": normalized["prior_context"]["pack_ref"]},
        "new_state": {"ref_id": CURRENT_STATE_REF, "object_id": current_id,
                       "integrity_sha256": _sha256(state_payload)},
        "accepted_revision": {"before": previous_revision, "after": git["commit_sha"]},
        "objective": {"before": previous_objective, "after": human["current_objective"]},
        "next_step": {"before": previous_next, "after": human["next_step"]},
        "recent_work_added": {"summary": human["recent_work"], "task_id": normalized["task_id"],
                              "root_run_id": normalized["run_id"], "outcome": "SUCCEEDED",
                              "outcome_source": "HUMAN_OPERATOR_ASSERTION",
                              "lifecycle_at_commit": {"task_status": "ACTIVE", "root_run_status": "RUNNING"}},
        "facts_superseded": _superseded_fact_keys(prior_doc, prior_state_id),
        "facts_unchanged": retained,
        "source_refs": source_refs,
        "responsible_task": {"task_id": normalized["task_id"], "root_run_id": normalized["run_id"]},
    }
    delta_payload = _canonical_json(changed)
    source_payloads = {current_id: state_payload, delta_id: delta_payload, **snapshot_payloads}
    source_classes = {current_id: current_class, delta_id: delta_class, **snapshot_classes}
    entries = [
        {"source_ref": current_id, "source_type": "artifact", "classification_assertion_ref": current_class["assertion_id"],
         "source_integrity_sha256": _sha256(state_payload), "content": state_payload.decode("utf-8")},
        {"source_ref": delta_id, "source_type": "artifact", "classification_assertion_ref": delta_class["assertion_id"],
         "source_integrity_sha256": _sha256(delta_payload), "content": delta_payload.decode("utf-8")},
        *snapshot_entries,
    ]
    entries.sort(key=lambda item: (_SOURCE_ORDER[item["source_type"]], item["source_ref"].encode("utf-8")))
    if len(entries) > 50:
        _fail("CONTINUATION_CONTEXT_SOURCE_LIMIT")
    basis = {
        "policy_id": "NEXUS_CONTEXT_SELECTION_V1",
        "selection_rule": "EXPLICIT_CANONICAL_REFS_PLUS_ADMITTED_MEMORY_QUERY",
        "ordering_rule": "SOURCE_TYPE_FIXED_ORDER_THEN_SOURCE_REF_UTF8_BYTE_ORDER",
        "source_ref_count": len(entries), "memory_query_sha256": None, "memory_limit": None,
        "ambient_host_memory_read": False, "academy_sources_read": False,
    }
    content_hash = _sha256(_canonical_json({"task_id": normalized["task_id"], "run_id": normalized["run_id"],
                                            "selection_basis": basis, "entries": entries}))
    context_doc = {
        "schema_id": "nexus.context_pack", "schema_version": 1, "task_id": normalized["task_id"],
        "run_id": normalized["run_id"], "selection_basis": basis, "entries": entries,
        "content_hash": content_hash, "model_visible_exposure": "UNKNOWN",
    }
    try:
        store._validate("nexus.context_pack@1.schema.json", context_doc)
    except Exception:
        _fail("CONTINUATION_CONTEXT_PREFLIGHT_FAILED")
    context_payload = _canonical_json(context_doc)
    if len(context_payload) >= _MAX_PACK_BYTES:
        _fail("CONTINUATION_CONTEXT_PREFLIGHT_FAILED")
    if any(len(payload) > _MAX_PACK_BYTES for payload in (state_payload, delta_payload)):
        _fail("CONTINUATION_PAYLOAD_LIMIT")
    return {
        "state_doc": state_doc, "state_payload": state_payload, "state_hash": _sha256(state_payload),
        "delta_doc": changed, "delta_payload": delta_payload, "delta_hash": _sha256(delta_payload),
        "context_doc": context_doc, "context_payload": context_payload,
        "context_hash": context_doc["content_hash"], "context_integrity_hash": _sha256(context_payload),
        "snapshot_payloads": snapshot_payloads, "snapshot_classes": snapshot_classes,
        "original_entries": original_entries, "source_payloads": source_payloads,
        "source_classes": source_classes, "current_class": current_class,
        "delta_class": delta_class, "pack_class": pack_class, "prior_state_entry": prior_state_entry,
        "prior_classification": prior_meta["classification"],
    }


def _request_document(normalized: dict[str, Any], projection: dict[str, Any], documents: dict[str, Any]) -> dict[str, Any]:
    stable = []
    for item in normalized["stable_sources"]:
        entry = documents["original_entries"][item["source_ref"]]
        stable.append({
            **item, "source_type": entry["source_type"],
            "source_integrity_sha256": entry["source_integrity_sha256"],
            "byte_size": len(entry["content"].encode("utf-8")),
            "snapshot_integrity_sha256": _sha256(documents["snapshot_payloads"][item["snapshot_object_id"]]),
            "snapshot_byte_size": len(documents["snapshot_payloads"][item["snapshot_object_id"]]),
            "classification": documents["snapshot_classes"][item["snapshot_object_id"]],
        })
    return {
        "protocol_version": PROTOCOL_VERSION, "command_id": normalized["command_id"],
        "instance_expectation": normalized["instance_expectation"],
        "operator_principal_id": normalized["operator_id"], "task_id": normalized["task_id"],
        "root_run_id": normalized["run_id"], "grant_id": normalized["grant_id"],
        "expected_previous_state": normalized["prior"], "expected_previous_context": normalized["prior_context"],
        "git_fact": normalized["git_fact"], "human_assertions": normalized["human_assertions"],
        "as_of": normalized["as_of"], "outputs": normalized["output_ids"],
        "classifications": normalized["classes"], "stable_sources": stable,
        "semantic_payload_sha256": {
            "current_state": documents["state_hash"], "what_changed": documents["delta_hash"],
            "context_content": documents["context_hash"], "context_payload": documents["context_integrity_hash"],
        },
        "authority": {
            "issued_by": normalized["operator_id"], "granted_to": projection["runtime_principal_id"],
            "parent_grant_id": projection["grant"]["parent_grant_id"],
            "policy_version": projection["grant"]["policy_version"],
            "issued_at": projection["grant"]["issued_at"],
            "expires_at": projection["grant"]["expires_at"],
            **({"credential_ref": projection["grant"]["credential_ref"]}
               if projection["grant"].get("credential_ref") is not None else {}),
            "task_scope": projection["scope"]["task_scope"],
            "resource_scope": projection["scope"]["resource_scope"],
            "action_scope": projection["scope"]["action_scope"],
            "audience_scope": projection["scope"]["audience_scope"],
        },
    }


def _confirmation_summary(normalized: dict[str, Any], projection: dict[str, Any],
                         documents: dict[str, Any]) -> dict[str, Any]:
    grant = projection["grant"]
    return {
        "task_id": normalized["task_id"], "root_run_id": normalized["run_id"],
        "task_status": projection["task"]["status"], "run_status": projection["run"]["status"],
        "work_outcome": "SUCCEEDED", "work_outcome_source": "HUMAN_OPERATOR_ASSERTION",
        "lifecycle_at_commit": {"task_status": projection["task"]["status"],
                                "root_run_status": projection["run"]["status"]},
        "grant_id": normalized["grant_id"], "grant_expires_at": grant["expires_at"],
        "instance_id": normalized["instance_expectation"]["instance_id"],
        "as_of": normalized["as_of"],
        "previous_current_state": {
            "ref_id": CURRENT_STATE_REF, "object_id": normalized["prior"]["object_id"],
            "integrity_sha256": normalized["prior"]["integrity_sha256"],
            "logical_ref_revision": normalized["prior"]["current_ref_revision"],
        },
        "previous_context": {
            "pack_ref": normalized["prior_context"]["pack_ref"],
            "content_hash": normalized["prior_context"]["content_hash"],
        },
        "outputs": {
            "current_state_ref": normalized["output_ids"]["current_state_object_id"],
            "current_state_classification": normalized["classes"]["current_state_classification"],
            "what_changed_ref": normalized["output_ids"]["what_changed_object_id"],
            "what_changed_classification": normalized["classes"]["what_changed_classification"],
            "context_pack_ref": normalized["output_ids"]["context_pack_object_id"],
            "context_pack_classification": normalized["classes"]["context_pack_classification"],
        },
        "git_fact": normalized["git_fact"],
        "human_assertions": normalized["human_assertions"],
        "stable_sources": [
            {"source_ref": item["source_ref"], "snapshot_object_id": item["snapshot_object_id"]}
            for item in normalized["stable_sources"]
        ],
        "required_action_scope": ["CLASSIFY", "INSPECT", "OBJECT_WRITE"],
        "required_audience_scope": ["nexus-inspect", "nexus-runtime"],
        "required_resource_scope": sorted(
            _resource_closure(normalized, normalized["prior"]["object_id"]),
            key=lambda value: value.encode("utf-8"),
        ),
        "resource_count": len(projection["scope"]["resource_scope"]),
        "data_boundary": projection["boundary"],
        "context_projected_byte_size": len(documents["context_payload"]),
        "model_visible_exposure": "UNKNOWN",
    }


def _assert_resource_barriers(store, normalized: dict[str, Any], prior_state_id: str) -> None:
    ids = [*normalized["output_ids"].values(), *(item["snapshot_object_id"] for item in normalized["stable_sources"]), prior_state_id]
    try:
        with store._connection() as conn:
            store._assert_unbarred_object_resources(conn, ids)
    except Exception:
        _fail("CONTINUATION_PURGE_OR_RECOVERY_UNSAFE")


def _check_pointer(store, normalized: dict[str, Any], *, request_bound: bool) -> dict[str, Any] | None:
    row = _read_current_ref(store)
    previous = normalized["prior"]
    expected_revision = previous["current_ref_revision"]
    new_id = normalized["output_ids"]["current_state_object_id"]
    if expected_revision is None:
        if row is None:
            return None
        if request_bound and row["ref_type"] == "artifact" and row["current_object_id"] == previous["object_id"] and row["revision"] == 1:
            return row
        if request_bound and row["ref_type"] == "artifact" and row["current_object_id"] == new_id and row["revision"] == 2:
            return row
        _fail("CONTINUATION_STATE_CONFLICT")
    if (row and row["ref_type"] == "artifact" and row["current_object_id"] == previous["object_id"]
            and row["revision"] == expected_revision):
        return row
    if (request_bound and row and row["ref_type"] == "artifact" and row["current_object_id"] == new_id
            and row["revision"] == expected_revision + 1):
        return row
    _fail("CONTINUATION_STATE_CONFLICT")


def _assert_ids_and_outputs(store, normalized: dict[str, Any], documents: dict[str, Any], *, request_bound: bool) -> None:
    expected_objects = {
        normalized["output_ids"]["current_state_object_id"]: ("artifact", documents["state_hash"], normalized["run_id"], normalized["classes"]["current_state_classification"]["assertion_id"]),
        normalized["output_ids"]["what_changed_object_id"]: ("artifact", documents["delta_hash"], normalized["run_id"], normalized["classes"]["what_changed_classification"]["assertion_id"]),
        normalized["output_ids"]["context_pack_object_id"]: ("artifact", documents["context_integrity_hash"], normalized["run_id"], normalized["classes"]["context_pack_classification"]["assertion_id"]),
    }
    expected_objects.update({
        item["snapshot_object_id"]: ("artifact",
                                     _sha256(documents["snapshot_payloads"][item["snapshot_object_id"]]),
                                     normalized["run_id"], item["classification_assertion_id"])
        for item in normalized["stable_sources"]
    })
    with store._connection() as conn:
        command_rows = {row[0] for row in conn.execute(
            "SELECT command_id FROM command_ledger WHERE command_id IN (%s)" % ",".join("?" for _ in normalized["command_ids"]),
            normalized["command_ids"],
        ).fetchall()}
        existing_ids = {row[0] for row in conn.execute(
            "SELECT object_id FROM objects WHERE object_id IN (%s)" % ",".join("?" for _ in expected_objects),
            list(expected_objects),
        ).fetchall()}
        class_ids = [*normalized["classes"].values()]
        class_ids = [item["assertion_id"] for item in class_ids] + [item["classification_assertion_id"] for item in normalized["stable_sources"]]
        existing_classes = {row[0] for row in conn.execute(
            "SELECT assertion_id FROM classification_assertions WHERE assertion_id IN (%s)" % ",".join("?" for _ in class_ids),
            class_ids,
        ).fetchall()}
    if not request_bound and (command_rows or existing_ids or existing_classes):
        _fail("CONTINUATION_ID_CONFLICT")
    if request_bound:
        unexpected_commands = set(command_rows) - set(normalized["command_ids"])
        if unexpected_commands:
            _fail("CONTINUATION_ID_CONFLICT")
    for object_id in existing_ids:
        object_type, integrity, created_by, class_ref = expected_objects[object_id]
        metadata = store.get_object_metadata(object_id)
        if (metadata.get("object_type"), metadata.get("integrity_hash"), metadata.get("created_by_run"),
                metadata.get("classification_assertion_ref")) != (object_type, integrity, created_by, class_ref):
            _fail("CONTINUATION_ID_CONFLICT")
    if existing_ids and not request_bound:
        _fail("CONTINUATION_ID_CONFLICT")


def _prepare(store, authority, context_packs, plan: dict[str, Any], git_repo: str | Path,
             *, request_bound_hint: bool | None = None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], bool]:
    normalized = _validate_plan_shape(store, authority, plan)
    _validate_binding(store, normalized["instance_expectation"])
    _validate_modes(store)
    has_request_binding = _request_binding_exists(store, normalized)
    projection = _read_projection(store, normalized, allow_historical=has_request_binding)
    request_bound = request_bound_hint if request_bound_hint is not None else False
    try:
        _check_work_closure(store, normalized["task_id"])
    except Exception as exc:
        reason = getattr(exc, "reason_code", None)
        _fail(reason if isinstance(reason, str) else "CONTINUATION_TASK_NOT_CLOSED_FOR_COMMIT")

    prior_pack, state_entry, prior_doc = _prior_context(context_packs, normalized)
    if prior_doc.get("as_of"):
        try:
            if normalized["as_of_dt"] <= _timestamp(prior_doc["as_of"]):
                _fail("CONTINUATION_TIMESTAMP_NOT_MONOTONIC")
        except ContinuationCommitError:
            raise
        except Exception:
            _fail("CONTINUATION_PREVIOUS_STATE_INVALID")

    stable_refs = {item["source_ref"] for item in normalized["stable_sources"]}
    for ref in stable_refs:
        if _pack_entry(prior_pack, ref) is None:
            _fail("CONTINUATION_CONTEXT_SOURCE_INVALID")
    if normalized["prior"]["object_id"] in stable_refs:
        _fail("CONTINUATION_CONTEXT_SOURCE_INVALID")
    _assert_resource_barriers(store, normalized, normalized["prior"]["object_id"])
    documents = _build_documents(store, normalized, projection, state_entry, prior_doc, prior_pack)
    _validate_scope(projection, normalized, normalized["prior"]["object_id"])
    request = _request_document(normalized, projection, documents)
    bound = _read_request(store, normalized, request)
    if not bound:
        if has_request_binding:
            _fail("CONTINUATION_REQUEST_BINDING_INVALID")
        _grant_chain(authority, normalized, projection, require_current=True)
    # A durable HUMAN-confirmed request owns its frozen Git observation. Exact
    # recovery must not depend on the repository still having that live state.
    if not bound:
        _validate_git_fact(git_repo, normalized["git_fact"])
    # A new compiled pack must sort newer than its predecessor under the
    # existing ContextPackService.latest ordering. Exact bound replay may
    # already have compiled this very output pack.
    try:
        latest = context_packs.latest()
        if latest.get("pack_id") != normalized["output_ids"]["context_pack_object_id"]:
            latest_compiled = _timestamp(latest["compiled_at"])
            if normalized["as_of_dt"] <= latest_compiled:
                _fail("CONTINUATION_TIMESTAMP_NOT_MONOTONIC")
    except ContinuationCommitError:
        raise
    except Exception:
        _fail("CONTINUATION_PREVIOUS_CONTEXT_UNAVAILABLE")
    if not bound and latest.get("pack_id") != normalized["prior_context"]["pack_ref"]:
        _fail("CONTINUATION_CONTEXT_CONFLICT")
    if bound and latest.get("pack_id") not in {
        normalized["prior_context"]["pack_ref"], normalized["output_ids"]["context_pack_object_id"],
    }:
        _fail("CONTINUATION_CONTEXT_CONFLICT")
    _check_pointer(store, normalized, request_bound=bound)
    if request_bound_hint is not None and bound != request_bound_hint:
        _fail("CONTINUATION_REQUEST_BINDING_INVALID")
    _assert_ids_and_outputs(store, normalized, documents, request_bound=bound)
    return normalized, projection, documents, request, bound


def prepare_continuation_commit(*, store, authority, context_packs, plan: dict[str, Any],
                                git_repo: str | Path) -> dict[str, Any]:
    """Read-only preflight; returns only a sanitized confirmation summary."""
    try:
        normalized, projection, documents, _request, bound = _prepare(
            store, authority, context_packs, plan, git_repo,
        )
        return {
            "request_bound": bound,
            "confirmation_phrase": None if bound else "COMMIT CONTINUATION " + normalized["task_id"],
            "summary": _confirmation_summary(normalized, projection, documents),
        }
    except ContinuationCommitError:
        raise
    except CommandConflict:
        _fail("COMMAND_CONFLICT")
    except Exception:
        _fail("CONTINUATION_PREFLIGHT_FAILED")


def _replay(store, command_id: str, operation: str, request: dict[str, Any]) -> dict[str, Any] | None:
    digest = store._request_hash(operation, request)
    with store._connection() as conn:
        return store._replay_command(conn, command_id, operation, digest)


def _authorize(authority, normalized: dict[str, Any], *, resource: str, action: str, audience: str,
               command_id: str) -> None:
    authority.evaluate_authorization(
        normalized["grant_id"],
        {"task": normalized["task_id"], "resource": resource, "action": action, "audience": audience},
        command_id,
    )


def _record_classification(store, authority, normalized: dict[str, Any], assertion: dict[str, Any], command_id: str) -> None:
    prior = _replay(store, command_id, "record_classification_assertion",
                    {"assertion": assertion, "grant_id": normalized["grant_id"]})
    if prior is not None:
        authority.record_classification_assertion(
            assertion, grant_id=normalized["grant_id"], task_id=normalized["task_id"],
            audience="nexus-runtime", command_id=command_id,
        )
        return
    authority.record_classification_assertion(
        assertion, grant_id=normalized["grant_id"], task_id=normalized["task_id"],
        audience="nexus-runtime", command_id=command_id,
    )


def _put(store, authority, normalized: dict[str, Any], *, object_id: str, payload: bytes,
         object_type: str, class_ref: str, derived_from: list[str], command_id: str) -> None:
    _authorize(authority, normalized, resource=object_id, action="OBJECT_WRITE", audience="nexus-runtime",
               command_id=command_id + ":authorize")
    store.put_object(command_id=command_id, object_id=object_id, payload=payload,
                     object_type=object_type, created_by_run=normalized["run_id"],
                     classification_assertion_ref=class_ref, derived_from=derived_from)


def _ensure_active(store, authority, normalized: dict[str, Any]) -> dict[str, Any]:
    _validate_binding(store, normalized["instance_expectation"])
    _validate_modes(store)
    projection = _read_projection(store, normalized)
    _grant_chain(authority, normalized, projection, require_current=True)
    _check_pointer(store, normalized, request_bound=True)
    return projection


def _result(normalized: dict[str, Any], documents: dict[str, Any], *, replayed: bool) -> dict[str, Any]:
    return {
        "status": "CONTINUATION_COMMITTED", "task_id": normalized["task_id"],
        "root_run_id": normalized["run_id"],
        "previous_current_state_ref": normalized["prior"]["object_id"],
        "previous_current_state_hash": normalized["prior"]["integrity_sha256"],
        "new_current_state_ref": normalized["output_ids"]["current_state_object_id"],
        "new_current_state_hash": documents["state_hash"],
        "what_changed_ref": normalized["output_ids"]["what_changed_object_id"],
        "what_changed_hash": documents["delta_hash"],
        "context_pack_ref": normalized["output_ids"]["context_pack_object_id"],
        "accepted_revision": normalized["git_fact"]["commit_sha"],
        "next_step": normalized["human_assertions"]["next_step"],
        "replayed": replayed,
    }


def _complete_replay(store, context_packs, normalized: dict[str, Any], documents: dict[str, Any]) -> dict[str, Any] | None:
    command_id = normalized["command_id"]
    operation_specs = [
        (command_id + ":put-current-state", "put_object", {
            "payload_integrity_hash": documents["state_hash"], "object_id": normalized["output_ids"]["current_state_object_id"],
            "object_type": "artifact", "created_by_run": normalized["run_id"],
            "classification_assertion_ref": normalized["classes"]["current_state_classification"]["assertion_id"],
            "derived_from": sorted({normalized["prior"]["object_id"], normalized["prior_context"]["pack_ref"],
                                     *(item["snapshot_object_id"] for item in normalized["stable_sources"])}),
        }),
        (command_id + ":put-what-changed", "put_object", {
            "payload_integrity_hash": documents["delta_hash"], "object_id": normalized["output_ids"]["what_changed_object_id"],
            "object_type": "artifact", "created_by_run": normalized["run_id"],
            "classification_assertion_ref": normalized["classes"]["what_changed_classification"]["assertion_id"],
            "derived_from": sorted({normalized["prior"]["object_id"], normalized["prior_context"]["pack_ref"],
                                     normalized["output_ids"]["current_state_object_id"],
                                     *(item["snapshot_object_id"] for item in normalized["stable_sources"])}),
        }),
    ]
    for index, item in enumerate(normalized["stable_sources"]):
        operation_specs.append((f"{command_id}:snapshot:{index:02d}:put", "put_object", {
            "payload_integrity_hash": _sha256(documents["snapshot_payloads"][item["snapshot_object_id"]]),
            "object_id": item["snapshot_object_id"], "object_type": "artifact",
            "created_by_run": normalized["run_id"],
            "classification_assertion_ref": item["classification_assertion_id"],
            "derived_from": [item["source_ref"]],
        }))
    all_commands = [_replay(store, cmd, op, req) for cmd, op, req in operation_specs]
    if any(item is None for item in all_commands):
        return None
    relation = _replay(store, command_id + ":supersedes", "add_relation", {
        "schema_id": "nexus.object_relation", "schema_version": 1,
        "from_id": normalized["output_ids"]["current_state_object_id"], "relation_type": "supersedes",
        "to_id": normalized["prior"]["object_id"],
    })
    ref_cas = _replay(store, command_id + ":current-ref-cas", "compare_and_swap_ref", {
        "ref_id": CURRENT_STATE_REF, "expected_revision": (normalized["prior"]["current_ref_revision"] or 1),
        "new_object_id": normalized["output_ids"]["current_state_object_id"], "updated_by_run": normalized["run_id"],
    })
    if relation is None or ref_cas is None:
        return None
    current_ref = _read_current_ref(store)
    expected_current_revision = (normalized["prior"]["current_ref_revision"] or 1) + 1
    if (not current_ref or current_ref["ref_type"] != "artifact"
            or current_ref["current_object_id"] != normalized["output_ids"]["current_state_object_id"]
            or current_ref["revision"] != expected_current_revision):
        return None
    with store._connection() as conn:
        supersession_exists = conn.execute(
            "SELECT 1 FROM object_relations WHERE from_id=? AND relation_type='supersedes' AND to_id=?",
            (normalized["output_ids"]["current_state_object_id"], normalized["prior"]["object_id"]),
        ).fetchone()
    if not supersession_exists:
        return None
    context_request = {
        "task_id": normalized["task_id"], "run_id": normalized["run_id"],
        "grant_id": normalized["grant_id"], "pack_object_id": normalized["output_ids"]["context_pack_object_id"],
        "classification_assertion_ref": normalized["classes"]["context_pack_classification"]["assertion_id"],
        "command_id": command_id + ":context", "source_refs": sorted(
            [normalized["output_ids"]["current_state_object_id"], normalized["output_ids"]["what_changed_object_id"],
             *(item["snapshot_object_id"] for item in normalized["stable_sources"])], key=lambda value: value.encode("utf-8")),
        "memory_query": None, "memory_limit": 20,
    }
    if _replay(store, command_id + ":context-object", "put_object", {
        "payload_integrity_hash": documents["context_integrity_hash"],
        "object_id": normalized["output_ids"]["context_pack_object_id"], "object_type": "artifact",
        "created_by_run": normalized["run_id"],
        "classification_assertion_ref": normalized["classes"]["context_pack_classification"]["assertion_id"],
        "derived_from": context_request["source_refs"],
    }) is None:
        return None
    with store._connection() as conn:
        row = conn.execute("SELECT task_id,run_id,content_hash,serialized_byte_size,state FROM context_pack_records WHERE pack_ref=?",
                           (normalized["output_ids"]["context_pack_object_id"],)).fetchone()
    if (not row or tuple(row) != (normalized["task_id"], normalized["run_id"], documents["context_hash"],
                                  len(documents["context_payload"]), "COMPILED")):
        return None
    try:
        verified_context = context_packs.read_compiled(normalized["output_ids"]["context_pack_object_id"])
        latest = context_packs.latest()
    except Exception:
        return None
    expected_refs = [entry["source_ref"] for entry in documents["context_doc"]["entries"]]
    if (latest.get("pack_id") != normalized["output_ids"]["context_pack_object_id"]
            or latest.get("model_visible_exposure") != "UNKNOWN"
            or verified_context.get("content_hash") != documents["context_hash"]
            or verified_context.get("integrity_hash") != documents["context_integrity_hash"]
            or verified_context.get("serialized_byte_size") != len(documents["context_payload"])
            or [entry["source_ref"] for entry in verified_context.get("entries", [])] != expected_refs
            or verified_context.get("entries") != documents["context_doc"]["entries"]):
        return None
    return _result(normalized, documents, replayed=True)


def commit_continuation(*, store, authority, context_packs, plan: dict[str, Any], git_repo: str | Path,
                        confirmation: Callable[[str, dict[str, Any]], bool],
                        _stage_hook: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Bind the HUMAN-approved request, write a deterministic state/delta, and compile Context."""
    store_locked = False
    human_confirmed = False
    try:
        normalized, projection, documents, request, request_bound = _prepare(
            store, authority, context_packs, plan, git_repo,
        )
        if request_bound:
            completed = _complete_replay(store, context_packs, normalized, documents)
            if completed is not None:
                return completed
        if not request_bound:
            phrase = "COMMIT CONTINUATION " + normalized["task_id"]
            if not confirmation(phrase, _confirmation_summary(normalized, projection, documents)):
                _fail("CONTINUATION_CONFIRMATION_DENIED")
            human_confirmed = True

        # Serialize the post-confirmation recheck, first durable binding, and
        # all owned writes against other operations sharing this ObjectStore.
        # The accepted writer lock excludes other processes; this lock closes
        # the in-process optimistic-CAS race as well.
        store._lock.acquire()
        store_locked = True
        normalized, projection, documents, request, request_bound = _prepare(
            store, authority, context_packs, plan, git_repo,
        )
        if request_bound:
            completed = _complete_replay(store, context_packs, normalized, documents)
            if completed is not None:
                return completed
        else:
            if not human_confirmed:
                _fail("CONTINUATION_REQUEST_BINDING_INVALID")
            store.bind_command_request(command_id=normalized["request_command_id"],
                                       operation="continuation_commit_request", request=request)
            request_bound = True
            if _stage_hook:
                _stage_hook("request_bound")

        cmd = normalized["command_id"]
        current_id = normalized["output_ids"]["current_state_object_id"]
        delta_id = normalized["output_ids"]["what_changed_object_id"]
        prior_id = normalized["prior"]["object_id"]
        snapshot_ids = [item["snapshot_object_id"] for item in normalized["stable_sources"]]

        for index, item in enumerate(normalized["stable_sources"]):
            current = _ensure_active(store, authority, normalized)
            classification = documents["snapshot_classes"][item["snapshot_object_id"]]
            class_cmd = f"{cmd}:snapshot:{index:02d}:class"
            _record_classification(store, authority, normalized, classification, class_cmd)
            _put(store, authority, normalized, object_id=item["snapshot_object_id"],
                 payload=documents["snapshot_payloads"][item["snapshot_object_id"]],
                 object_type="artifact",
                 class_ref=item["classification_assertion_id"], derived_from=[item["source_ref"]],
                 command_id=f"{cmd}:snapshot:{index:02d}:put")
            if _stage_hook:
                _stage_hook(f"snapshot:{index:02d}")

        _ensure_active(store, authority, normalized)
        current_class = documents["current_class"]
        _record_classification(store, authority, normalized, current_class, cmd + ":class-current-state")
        _put(store, authority, normalized, object_id=current_id, payload=documents["state_payload"],
             object_type="artifact", class_ref=current_class["assertion_id"],
             derived_from=sorted({prior_id, normalized["prior_context"]["pack_ref"], *snapshot_ids}),
             command_id=cmd + ":put-current-state")
        if _stage_hook:
            _stage_hook("current_state_written")

        _ensure_active(store, authority, normalized)
        delta_class = documents["delta_class"]
        _record_classification(store, authority, normalized, delta_class, cmd + ":class-what-changed")
        _put(store, authority, normalized, object_id=delta_id, payload=documents["delta_payload"],
             object_type="artifact", class_ref=delta_class["assertion_id"],
             derived_from=sorted({prior_id, normalized["prior_context"]["pack_ref"], current_id, *snapshot_ids}),
             command_id=cmd + ":put-what-changed")
        if _stage_hook:
            _stage_hook("what_changed_written")

        _ensure_active(store, authority, normalized)
        relation_resource = f"relation:{current_id}:supersedes:{prior_id}"
        _authorize(authority, normalized, resource=relation_resource, action="OBJECT_WRITE",
                   audience="nexus-runtime", command_id=cmd + ":supersedes-authorize")
        store.add_relation(command_id=cmd + ":supersedes", from_id=current_id,
                           relation_type="supersedes", to_id=prior_id)
        if _stage_hook:
            _stage_hook("supersession_written")

        pointer = _read_current_ref(store)
        expected_rev = normalized["prior"]["current_ref_revision"]
        _ensure_active(store, authority, normalized)
        _authorize(authority, normalized, resource="logical-ref:" + CURRENT_STATE_REF,
                   action="OBJECT_WRITE", audience="nexus-runtime", command_id=cmd + ":current-ref-authorize")
        if expected_rev is None and pointer is None:
            store.create_logical_ref(command_id=cmd + ":current-ref-init", ref_id=CURRENT_STATE_REF,
                                     ref_type="artifact", object_id=prior_id, updated_by_run=normalized["run_id"])
            expected_cas_revision = 1
            if _stage_hook:
                _stage_hook("current_ref_initialized")
        elif expected_rev is not None and pointer and pointer["revision"] == expected_rev and pointer["current_object_id"] == prior_id:
            expected_cas_revision = expected_rev
        else:
            # The command may already have committed the pointer CAS on exact replay.
            expected_cas_revision = expected_rev or 1
        store.compare_and_swap_ref(command_id=cmd + ":current-ref-cas", ref_id=CURRENT_STATE_REF,
                                   expected_revision=expected_cas_revision, new_object_id=current_id,
                                   updated_by_run=normalized["run_id"])
        if _stage_hook:
            _stage_hook("current_ref_updated")

        _ensure_active(store, authority, normalized)
        pack_class = documents["pack_class"]
        _record_classification(store, authority, normalized, pack_class, cmd + ":class-context-pack")
        if _stage_hook:
            _stage_hook("context_classified")
        context_refs = sorted([current_id, delta_id, *snapshot_ids], key=lambda value: value.encode("utf-8"))
        context_result = context_packs.compile(
            task_id=normalized["task_id"], run_id=normalized["run_id"], grant_id=normalized["grant_id"],
            pack_object_id=normalized["output_ids"]["context_pack_object_id"],
            classification_assertion_ref=pack_class["assertion_id"], command_id=cmd + ":context",
            source_refs=context_refs, memory_query=None,
        )
        if (context_result.get("pack_id") != normalized["output_ids"]["context_pack_object_id"]
                or context_result.get("content_hash") != documents["context_hash"]
                or context_result.get("integrity_hash") != documents["context_integrity_hash"]
                or context_result.get("model_visible_exposure") != "UNKNOWN"
                or context_result.get("selected_refs") != [entry["source_ref"] for entry in documents["context_doc"]["entries"]]):
            _fail("CONTINUATION_CONTEXT_COMMIT_MISMATCH")
        if _stage_hook:
            _stage_hook("context_compiled")
        latest = context_packs.latest()
        if latest.get("pack_id") != normalized["output_ids"]["context_pack_object_id"]:
            _fail("CONTINUATION_CONTEXT_NOT_LATEST")
        return _result(normalized, documents, replayed=False)
    except ContinuationCommitError:
        raise
    except CommandConflict:
        _fail("COMMAND_CONFLICT")
    except (AuthorizationDenied, InvalidDelegation, RuntimeDenied) as exc:
        reason = exc.args[0] if exc.args else "CONTINUATION_COMMIT_DENIED"
        if isinstance(reason, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", reason):
            _fail(reason)
        _fail("CONTINUATION_COMMIT_DENIED")
    except Exception:
        _fail("CONTINUATION_COMMIT_FAILED")
    finally:
        if store_locked:
            store._lock.release()
