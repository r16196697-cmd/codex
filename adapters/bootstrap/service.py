"""Small, explicit application boundary for initializing and bootstrapping instances."""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Callable
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker

from adapters.storage import ObjectStore, StartupPurpose
from adapters.bootstrap.intents import create_durably, read_intent
from kernel.authority import AuthorityService
from kernel.authority.service import KERNEL_RECOVERY_PRINCIPAL_ID
from kernel.instance_binding import BOOTSTRAP_PROTOCOL_VERSION, parse_json_object, policy_sha256, request_sha256
from kernel.object.errors import MigrationError


FRESH_INTENT_NAME = ".nexus-instance-init-v1.json"
ADOPTION_INTENT_NAME = ".nexus-policy-adoption-v1.json"
AUTHORITY_INTENT_NAME = ".nexus-authority-bootstrap-v1.json"
_SAFE_GRANT_ACTIONS = {
    "RUN_CREATE", "RUN_TRANSITION", "OBJECT_WRITE", "TRACE_APPEND", "CLASSIFY",
    "INSPECT", "VERIFY", "MEMORY_ADMIT", "MEMORY_SEARCH",
}
_LOGICAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _is_safe_logical_id(value: Any) -> bool:
    """Reject path-shaped values before they can enter durable bootstrap facts."""
    if not isinstance(value, str) or not _LOGICAL_ID.fullmatch(value):
        return False
    return not Path(value).is_absolute() and not PureWindowsPath(value).is_absolute() and not PureWindowsPath(value).drive


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("TIMESTAMP_TIMEZONE_REQUIRED")
    return parsed.astimezone(timezone.utc)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _canonical_path(path: str | Path, *, require_parent: bool = True) -> Path:
    supplied = Path(path).expanduser()
    if supplied.is_symlink():
        raise MigrationError("BOOTSTRAP_PATH_SYMLINK_DENIED")
    absolute = Path(os.path.abspath(supplied))
    if require_parent and not absolute.parent.is_dir():
        raise MigrationError("BOOTSTRAP_PATH_PARENT_MISSING")
    try:
        resolved = absolute.resolve(strict=False)
    except OSError as exc:
        raise MigrationError("BOOTSTRAP_PATH_UNRESOLVED") from exc
    if os.path.normcase(str(absolute)) != os.path.normcase(str(resolved)):
        raise MigrationError("BOOTSTRAP_PATH_ALIAS_DENIED")
    current = absolute
    while current != current.parent:
        if current.is_symlink():
            raise MigrationError("BOOTSTRAP_PATH_SYMLINK_DENIED")
        current = current.parent
    return resolved


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _safe_root_and_journal(data_root: str | Path, journal_path: str | Path, *, fresh: bool) -> tuple[Path, Path]:
    root = _canonical_path(data_root)
    journal = _canonical_path(journal_path)
    repo = _repo_root().resolve()
    if _is_within(root, repo) or root == repo:
        raise MigrationError("BOOTSTRAP_ROOT_MUST_BE_REPO_EXTERNAL")
    if _is_within(journal, repo) or journal == repo:
        raise MigrationError("PURGE_JOURNAL_MUST_BE_REPO_EXTERNAL")
    if journal == root or _is_within(journal, root):
        raise MigrationError("PURGE_JOURNAL_MUST_BE_ROOT_EXTERNAL")
    if journal == root or root == journal:
        raise MigrationError("BOOTSTRAP_ROOT_JOURNAL_ALIAS")
    # A fresh bootstrap can only start against a journal with no history.
    # Check this before creating the root directory or durable intent so an
    # invalid journal cannot strand the target as a partial initialization.
    if journal.exists() and not journal.is_file():
        raise MigrationError("PURGE_JOURNAL_PATH_INVALID")
    if journal.exists():
        try:
            journal_stat = journal.stat()
            if journal_stat.st_nlink > 1:
                raise MigrationError("PURGE_JOURNAL_PATH_ALIAS_DENIED")
            if root.exists():
                for candidate in (root / "nexus.sqlite", root / FRESH_INTENT_NAME, root / ADOPTION_INTENT_NAME):
                    if candidate.exists() and journal.samefile(candidate):
                        raise MigrationError("PURGE_JOURNAL_PATH_ALIAS_DENIED")
            if fresh and journal_stat.st_size > 0:
                raise MigrationError("FRESH_PURGE_JOURNAL_CONFLICT")
        except OSError as exc:
            raise MigrationError("PURGE_JOURNAL_PATH_UNRESOLVED") from exc
    if fresh:
        if root.exists() and not root.is_dir():
            raise MigrationError("FRESH_INSTANCE_ROOT_NOT_DIRECTORY")
        if not root.exists():
            root.mkdir()
        # Unknown entries are never deleted or reinterpreted as a fresh root.
        allowed = {
            FRESH_INTENT_NAME, "nexus.sqlite", "nexus.sqlite-wal", "nexus.sqlite-shm",
            "nexus.writer.lock", "objects",
        }
        entries = list(root.iterdir())
        if any(item.is_symlink() for item in entries):
            raise MigrationError("FRESH_INSTANCE_ROOT_SYMLINK_ENTRY")
        if any(item.name not in allowed for item in entries):
            raise MigrationError("FRESH_INSTANCE_ROOT_NOT_EMPTY")
        if (root / "objects").exists():
            objects = root / "objects"
            if not objects.is_dir():
                raise MigrationError("FRESH_INSTANCE_OBJECT_ROOT_INVALID")
            for item in objects.rglob("*"):
                if item.is_symlink():
                    raise MigrationError("FRESH_INSTANCE_OBJECT_ROOT_INVALID")
                if item.is_file():
                    raise MigrationError("FRESH_INSTANCE_ROOT_NOT_EMPTY")
                if item.name != "sha256":
                    raise MigrationError("FRESH_INSTANCE_OBJECT_ROOT_INVALID")
        if not (root / FRESH_INTENT_NAME).exists() and entries:
            raise MigrationError("FRESH_INSTANCE_ROOT_NOT_EMPTY")
        if (root / "nexus.sqlite").exists() and not (root / FRESH_INTENT_NAME).exists():
            raise MigrationError("FRESH_INSTANCE_DATABASE_ALREADY_EXISTS")
    else:
        if not (root / "nexus.sqlite").is_file():
            raise MigrationError("LEGACY_INSTANCE_REQUIRED")
    return root, journal


def _load_validated_policy(path: str | Path) -> tuple[dict[str, Any], str]:
    policy_path = _canonical_path(path)
    try:
        policy = parse_json_object(policy_path.read_bytes())
        schema_path = _repo_root() / "policies" / "nexus.policy@1.schema.json"
        schema = parse_json_object(schema_path.read_bytes())
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(policy)
        if not _is_safe_logical_id(policy["policy_version"]):
            raise ValueError("policy version must be a safe logical identifier")
    except Exception as exc:
        if isinstance(exc, MigrationError):
            raise
        raise MigrationError("POLICY_INVALID") from exc
    return policy, policy_sha256(policy)


def _journal_identity(path: Path) -> str:
    material = "nexus-independent-purge-journal-v1\0" + os.path.normcase(str(path))
    import hashlib

    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _base_intent(*, source: str, instance_id: str, command_id: str, policy: dict[str, Any], digest: str, journal_identity: str, created_at: str | None = None) -> dict[str, Any]:
    body = {
        "protocol_version": BOOTSTRAP_PROTOCOL_VERSION,
        "source": source,
        "instance_id": instance_id,
        "command_id": command_id,
        "policy_version": policy["policy_version"],
        "policy_sha256": digest,
        "journal_identity": journal_identity,
    }
    result = dict(body)
    result["request_sha256"] = request_sha256(body)
    result["created_at"] = created_at or _utc_now()
    return result


def _reuse_or_create_intent(path: Path, *, source: str, command_id: str, policy: dict[str, Any], digest: str, journal_identity: str) -> dict[str, Any]:
    prior = read_intent(path)
    if prior is not None:
        expected = _base_intent(
            source=source,
            instance_id=prior.get("instance_id", ""),
            command_id=command_id,
            policy=policy,
            digest=digest,
            journal_identity=journal_identity,
            created_at=prior.get("created_at"),
        )
        if prior != expected:
            raise MigrationError("BOOTSTRAP_INTENT_CONFLICT")
        return prior
    intent = _base_intent(
        source=source,
        instance_id="nexus-instance-" + str(uuid4()),
        command_id=command_id,
        policy=policy,
        digest=digest,
        journal_identity=journal_identity,
    )
    try:
        create_durably(path, intent)
    except FileExistsError:
        return _reuse_or_create_intent(path, source=source, command_id=command_id,
                                       policy=policy, digest=digest, journal_identity=journal_identity)
    return intent


def _require_confirmation(action: str, summary: dict[str, Any], expected: str, confirmation: Callable[[str, dict[str, Any]], bool] | None) -> None:
    if confirmation is not None:
        if not confirmation(expected, summary):
            raise MigrationError("OPERATOR_CONFIRMATION_DENIED")
        return
    if not bool(getattr(sys.stdin, "isatty", lambda: False)()) or not bool(getattr(sys.stdout, "isatty", lambda: False)()):
        raise MigrationError("INTERACTIVE_TTY_REQUIRED")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    try:
        answer = input(f"Type {expected} to confirm: ")
    except EOFError as exc:
        raise MigrationError("OPERATOR_CONFIRMATION_DENIED") from exc
    if answer != expected:
        raise MigrationError("OPERATOR_CONFIRMATION_DENIED")


def initialize_instance(*, data_root: str | Path, policy_path: str | Path, independent_purge_journal: str | Path, command_id: str) -> dict[str, Any]:
    if not _is_safe_logical_id(command_id):
        raise MigrationError("INITIALIZATION_COMMAND_ID_REQUIRED")
    policy, digest = _load_validated_policy(policy_path)
    if len(policy["trust_anchors"]) != 1:
        raise MigrationError("INITIAL_POLICY_REQUIRES_ONE_OPERATOR_TRUST_ANCHOR")
    root, journal_path = _safe_root_and_journal(data_root, independent_purge_journal, fresh=True)
    journal_identity = _journal_identity(journal_path)
    intent = _reuse_or_create_intent(
        root / FRESH_INTENT_NAME,
        source="FRESH_INITIALIZE",
        command_id=command_id,
        policy=policy,
        digest=digest,
        journal_identity=journal_identity,
    )
    store = None
    try:
        store = ObjectStore(
            root,
            policy=policy,
            independent_purge_journal_path=journal_path,
            startup_purpose=StartupPurpose.INSTANCE_INITIALIZE,
        )
        binding = store.get_instance_binding_status()
        with store._connection() as conn:
            schema_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if binding["state"] != "FRESH_BOUND_INSTANCE":
            raise MigrationError("INSTANCE_INITIALIZATION_INCOMPLETE")
        return {
            "status": "INITIALIZED_UNAUTHORIZED",
            "instance_id": binding["instance_id"],
            "policy_version": binding["policy_version"],
            "policy_sha256": binding["policy_sha256"],
            "journal_identity": binding["journal_identity"],
            "schema_version": schema_version,
            "binding_command_id": intent["command_id"],
        }
    finally:
        if store is not None:
            store.close()


def _adoption_intent(*, root: Path, command_id: str, policy: dict[str, Any], digest: str, journal_identity: str) -> dict[str, Any]:
    return _reuse_or_create_intent(
        root / ADOPTION_INTENT_NAME,
        source="LEGACY_OPERATOR_ADOPTION",
        command_id=command_id,
        policy=policy,
        digest=digest,
        journal_identity=journal_identity,
    )


def adopt_policy_binding(
    *,
    data_root: str | Path,
    policy_path: str | Path,
    independent_purge_journal: str | Path,
    command_id: str,
    confirmation: Callable[[str, dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    if not _is_safe_logical_id(command_id):
        raise MigrationError("ADOPTION_COMMAND_ID_REQUIRED")
    policy, digest = _load_validated_policy(policy_path)
    root, journal_path = _safe_root_and_journal(data_root, independent_purge_journal, fresh=False)
    journal_identity = _journal_identity(journal_path)
    store = ObjectStore(
        root,
        policy=policy,
        independent_purge_journal_path=journal_path,
        startup_purpose=StartupPurpose.LEGACY_ADOPTION,
    )
    try:
        if store.instance_binding is not None:
            binding = store.instance_binding
            if binding["binding_command_id"] != command_id:
                raise MigrationError("INSTANCE_BINDING_CONFLICT")
            return {
                "status": "LEGACY_ADOPTED_BOUND_INSTANCE",
                "instance_id": binding["instance_id"],
                "policy_version": binding["policy_version"],
                "policy_sha256": binding["policy_sha256"],
                "journal_identity": binding["journal_identity"],
                "bound_at": binding["bound_at"],
            }

        summary = store.legacy_adoption_compatibility(policy)
        if not summary["compatible"]:
            raise MigrationError("LEGACY_ADOPTION_INCOMPATIBLE:" + ",".join(summary["issues"]))
        expected = "ADOPT " + store._startup_intent["instance_id"][-12:] if store._startup_intent else "ADOPT LEGACY POLICY"
        prompt_summary = {
            "status": "LEGACY_UNBOUND_INSTANCE",
            "policy_version": policy["policy_version"],
            "policy_sha256": digest,
            "journal_identity": journal_identity,
            "compatibility": summary,
        }
        _require_confirmation("adopt-policy-binding", prompt_summary, expected, confirmation)
        intent = _adoption_intent(root=root, command_id=command_id, policy=policy, digest=digest, journal_identity=journal_identity)
        store.accept_legacy_adoption_intent(intent)
        store.prepare_legacy_adoption()
        post_migration = store.legacy_adoption_compatibility(policy)
        if not post_migration["compatible"]:
            raise MigrationError("LEGACY_ADOPTION_INCOMPATIBLE:" + ",".join(post_migration["issues"]))
        binding = store.commit_legacy_policy_binding()
        return {
            "status": "LEGACY_ADOPTED_BOUND_INSTANCE",
            "instance_id": binding["instance_id"],
            "policy_version": binding["policy_version"],
            "policy_sha256": binding["policy_sha256"],
            "journal_identity": binding["journal_identity"],
            "bound_at": binding["bound_at"],
            "source": "LEGACY_OPERATOR_ADOPTION",
        }
    finally:
        store.close()


def _normalize_authority_plan(plan: dict[str, Any], policy: dict[str, Any], instance_id: str, policy_hash: str) -> dict[str, Any]:
    required = {
        "operator_principal_id", "runtime_principal_id", "anchor_id", "grant_id", "task_id",
        "resource_scope", "action_scope", "audience_scope", "issued_at", "expires_at", "command_id_prefix",
    }
    if set(plan) != required:
        raise MigrationError("AUTHORITY_BOOTSTRAP_PLAN_INVALID")
    ids = [plan[name] for name in ("operator_principal_id", "runtime_principal_id", "anchor_id", "grant_id", "task_id", "command_id_prefix")]
    if any(not _is_safe_logical_id(value) for value in ids):
        raise MigrationError("AUTHORITY_BOOTSTRAP_PLAN_INVALID")
    if KERNEL_RECOVERY_PRINCIPAL_ID in {plan["operator_principal_id"], plan["runtime_principal_id"]}:
        raise MigrationError("KERNEL_RECOVERY_IDENTITY_RESERVED")
    if plan["operator_principal_id"] == plan["runtime_principal_id"]:
        raise MigrationError("AUTHORITY_BOOTSTRAP_PRINCIPALS_MUST_DIFFER")
    if plan["operator_principal_id"] not in policy["trust_anchors"]:
        raise MigrationError("TRUST_ANCHOR_NOT_CONFIGURED_BY_POLICY")
    if not isinstance(plan["resource_scope"], list) or not plan["resource_scope"]:
        raise MigrationError("AUTHORITY_BOOTSTRAP_RESOURCE_SCOPE_INVALID")
    if len(plan["resource_scope"]) > 16:
        raise MigrationError("AUTHORITY_BOOTSTRAP_RESOURCE_SCOPE_TOO_BROAD")
    if any(not _is_safe_logical_id(item) or "*" in item for item in plan["resource_scope"]):
        raise MigrationError("AUTHORITY_BOOTSTRAP_WILDCARD_SCOPE_DENIED")
    if not isinstance(plan["action_scope"], list) or not plan["action_scope"]:
        raise MigrationError("AUTHORITY_BOOTSTRAP_ACTION_SCOPE_INVALID")
    if any(not isinstance(item, str) or item not in _SAFE_GRANT_ACTIONS for item in plan["action_scope"]):
        raise MigrationError("AUTHORITY_BOOTSTRAP_FORBIDDEN_ACTION")
    if not isinstance(plan["audience_scope"], list) or not plan["audience_scope"]:
        raise MigrationError("AUTHORITY_BOOTSTRAP_AUDIENCE_SCOPE_INVALID")
    if any(not isinstance(item, str) or item not in {"nexus-runtime", "nexus-inspect"} for item in plan["audience_scope"]):
        raise MigrationError("AUTHORITY_BOOTSTRAP_AUDIENCE_SCOPE_INVALID")
    try:
        issued = _parse_timestamp(plan["issued_at"])
        expires = _parse_timestamp(plan["expires_at"])
    except Exception as exc:
        raise MigrationError("AUTHORITY_BOOTSTRAP_EXPIRY_INVALID") from exc
    if expires <= issued:
        raise MigrationError("AUTHORITY_BOOTSTRAP_EXPIRY_INVALID")
    normalized = dict(plan)
    normalized["resource_scope"] = sorted(set(plan["resource_scope"]))
    normalized["action_scope"] = sorted(set(plan["action_scope"]))
    normalized["audience_scope"] = sorted(set(plan["audience_scope"]))
    normalized.update({"instance_id": instance_id, "policy_sha256": policy_hash, "policy_version": policy["policy_version"]})
    return normalized


def authority_bootstrap(
    *,
    data_root: str | Path,
    policy_path: str | Path,
    independent_purge_journal: str | Path,
    plan: dict[str, Any],
    confirmation: Callable[[str, dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    policy, digest = _load_validated_policy(policy_path)
    root, journal_path = _safe_root_and_journal(data_root, independent_purge_journal, fresh=False)
    store = ObjectStore(root, policy=policy, independent_purge_journal_path=journal_path)
    try:
        binding = store.get_instance_binding_status()
        if binding["state"] not in {"FRESH_BOUND_INSTANCE", "LEGACY_ADOPTED_BOUND_INSTANCE"}:
            raise MigrationError("INSTANCE_BINDING_REQUIRED")
        normalized = _normalize_authority_plan(plan, policy, binding["instance_id"], digest)
        body = {
            "protocol_version": BOOTSTRAP_PROTOCOL_VERSION,
            "instance_id": binding["instance_id"],
            "policy_sha256": digest,
            "policy_version": policy["policy_version"],
            "plan": normalized,
        }
        body["request_sha256"] = request_sha256(body)
        intent_path = root / AUTHORITY_INTENT_NAME
        prior = read_intent(intent_path)
        if prior is not None:
            if prior != body | {"created_at": prior.get("created_at")}:
                raise MigrationError("AUTHORITY_BOOTSTRAP_INTENT_CONFLICT")
            body = prior

        prefix = normalized["command_id_prefix"]
        human_id = normalized["operator_principal_id"]
        service_id = normalized["runtime_principal_id"]
        grant = {
            "schema_id": "nexus.delegation_grant", "schema_version": 1,
            "grant_id": normalized["grant_id"], "issued_by": human_id, "granted_to": service_id,
            "task_scope": [normalized["task_id"]],
            "resource_scope": normalized["resource_scope"],
            "action_scope": normalized["action_scope"],
            "audience_scope": normalized["audience_scope"],
            "issued_at": normalized["issued_at"], "expires_at": normalized["expires_at"],
            "status": "ACTIVE", "policy_version": policy["policy_version"],
        }
        grant_command_id = prefix + ":grant"
        grant_request_hash = store._request_hash("create_grant", grant)
        # A committed Grant is an immutable historical result. Replay it
        # before mutable time/authority gates so natural expiry never causes
        # this bootstrap plan to issue a replacement Grant.
        with store._connection() as conn:
            committed_grant = store._replay_command(
                conn, grant_command_id, "create_grant", grant_request_hash
            )
        if committed_grant is not None:
            return {
                "status": "AUTHORITY_BOOTSTRAP_COMPLETE",
                "instance_id": binding["instance_id"],
                "operator_principal_id": human_id,
                "runtime_principal_id": service_id,
                "trust_anchor_id": normalized["anchor_id"],
                "grant_id": normalized["grant_id"],
                "task_scope": [normalized["task_id"]],
                "expires_at": normalized["expires_at"],
            }

        now = datetime.now(timezone.utc)
        issued = _parse_timestamp(normalized["issued_at"])
        expires = _parse_timestamp(normalized["expires_at"])
        if not issued <= now < expires:
            raise MigrationError("AUTHORITY_BOOTSTRAP_PLAN_NOT_CURRENT")
        if prior is None:
            body["created_at"] = _utc_now()
        expected = "BOOTSTRAP " + binding["instance_id"][-12:]
        _require_confirmation("authority-bootstrap", {"status": "BOUND", "plan": normalized}, expected, confirmation)
        if prior is None:
            try:
                create_durably(intent_path, body)
            except FileExistsError:
                stored = read_intent(intent_path)
                if stored != body:
                    raise MigrationError("AUTHORITY_BOOTSTRAP_INTENT_CONFLICT")
                body = stored

        authority = AuthorityService(store, policy)
        authority.register_principal({
            "schema_id": "nexus.principal", "schema_version": 1,
            "principal_id": human_id, "principal_type": "HUMAN", "status": "ACTIVE",
        }, prefix + ":human")
        authority.register_principal({
            "schema_id": "nexus.principal", "schema_version": 1,
            "principal_id": service_id, "principal_type": "SERVICE", "status": "ACTIVE",
        }, prefix + ":service")
        authority.register_trust_anchor({
            "schema_id": "nexus.trust_anchor", "schema_version": 1,
            "anchor_id": normalized["anchor_id"], "principal_id": human_id,
            "policy_ref": policy["policy_version"],
        }, prefix + ":anchor")
        # The prefix above can take time or resume after a crash. Recheck
        # immediately before the first possible Grant commit.
        now = datetime.now(timezone.utc)
        if not issued <= now < expires:
            raise MigrationError("AUTHORITY_BOOTSTRAP_PLAN_NOT_CURRENT")
        authority.create_grant(grant, grant_command_id)
        return {
            "status": "AUTHORITY_BOOTSTRAP_COMPLETE",
            "instance_id": binding["instance_id"],
            "operator_principal_id": human_id,
            "runtime_principal_id": service_id,
            "trust_anchor_id": normalized["anchor_id"],
            "grant_id": normalized["grant_id"],
            "task_scope": [normalized["task_id"]],
            "expires_at": normalized["expires_at"],
        }
    finally:
        store.close()
