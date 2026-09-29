"""Import one exact local Git blob as a governed Artifact and provenance Evidence.

This adapter only reads the supplied repository's local object database. It does
not fetch, check out, scan, or read working-tree file contents.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sqlite3
import subprocess
from typing import Any

from kernel.authority.errors import ApprovalDenied, AuthorizationDenied, InvalidDelegation
from kernel.object.errors import CommandConflict, NexusStoreError
from kernel.run.errors import TraceAdmissionDenied


_LOGICAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_OID = re.compile(r"^[0-9a-f]+$")
_SENSITIVITY = {"PUBLIC", "PERSONAL", "PROJECT_PRIVATE", "CONFIDENTIAL", "SECRET"}
_TERMINAL_RUN_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}


class GitSourceImportError(Exception):
    """Stable, path-free error emitted by the local Git source adapter."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


def _deny(reason_code: str) -> None:
    raise GitSourceImportError(reason_code)


def _logical_id(value: Any) -> bool:
    return isinstance(value, str) and _LOGICAL_ID.fullmatch(value) is not None


def _validate_plan(plan: Any) -> dict[str, Any]:
    required = {
        "repository_id", "commit_oid", "path", "task_id", "run_id", "grant_id",
        "artifact_object_id", "artifact_classification_assertion_id", "evidence_object_id",
        "evidence_classification_assertion_id", "sensitivity_level", "handling_tags",
        "command_id_prefix",
    }
    if not isinstance(plan, dict) or set(plan) != required:
        _deny("GIT_SOURCE_PLAN_INVALID")
    for field in (
        "repository_id", "task_id", "run_id", "grant_id", "artifact_object_id",
        "artifact_classification_assertion_id", "evidence_object_id",
        "evidence_classification_assertion_id",
    ):
        if not _logical_id(plan[field]):
            _deny("GIT_SOURCE_PLAN_INVALID")
    prefix = plan["command_id_prefix"]
    if not _logical_id(prefix) or len(prefix) > 96:
        _deny("GIT_SOURCE_PLAN_INVALID")
    for suffix in ("classify-artifact", "authorize-artifact", "artifact",
                   "classify-evidence", "authorize-evidence", "evidence"):
        if len(prefix + ":" + suffix) > 128:
            _deny("GIT_SOURCE_PLAN_INVALID")
    if not isinstance(plan["sensitivity_level"], str) or plan["sensitivity_level"] not in _SENSITIVITY:
        _deny("GIT_SOURCE_PLAN_INVALID")
    tags = plan["handling_tags"]
    if (
        not isinstance(tags, list)
        or any(not isinstance(tag, str) or not tag or len(tag) > 128 for tag in tags)
        or len(tags) != len(set(tags))
    ):
        _deny("GIT_SOURCE_PLAN_INVALID")
    path = plan["path"]
    if (
        not isinstance(path, str)
        or not path
        or "\x00" in path
        or "\\" in path
        or path.startswith("/")
        or PureWindowsPath(path).is_absolute()
        or bool(PureWindowsPath(path).drive)
        or PurePosixPath(path).is_absolute()
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        _deny("GIT_SOURCE_PATH_INVALID")
    try:
        path.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        _deny("GIT_SOURCE_PATH_INVALID")
    if not isinstance(plan["commit_oid"], str):
        _deny("GIT_SOURCE_COMMIT_INVALID")
    return dict(plan)


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_NO_LAZY_FETCH"] = "1"
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def _run_git(repo_root: Path, *args: str, failure_code: str = "GIT_SOURCE_OBJECT_UNAVAILABLE") -> bytes:
    try:
        result = subprocess.run(
            ["git", *args], cwd=repo_root, env=_git_env(),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False, shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        _deny(failure_code)
    if result.returncode != 0:
        _deny(failure_code)
    return result.stdout


def _validated_repo_root(repository_path: str | os.PathLike[str]) -> Path:
    try:
        supplied = Path(repository_path).expanduser().absolute()
        if not supplied.exists() or not supplied.is_dir():
            _deny("GIT_SOURCE_REPOSITORY_INVALID")
        resolved = supplied.resolve(strict=True)
        if os.path.normcase(str(supplied)) != os.path.normcase(str(resolved)):
            _deny("GIT_SOURCE_REPOSITORY_INVALID")
        current = supplied
        while current != current.parent:
            if current.is_symlink():
                _deny("GIT_SOURCE_REPOSITORY_INVALID")
            current = current.parent
        top = _run_git(resolved, "rev-parse", "--show-toplevel", failure_code="GIT_SOURCE_REPOSITORY_INVALID").decode("utf-8", errors="strict").strip()
        top_path = Path(top).resolve(strict=True)
        inside = _run_git(resolved, "rev-parse", "--is-inside-work-tree", failure_code="GIT_SOURCE_REPOSITORY_INVALID").decode("ascii", errors="strict").strip()
        if inside != "true" or os.path.normcase(str(top_path)) != os.path.normcase(str(resolved)):
            _deny("GIT_SOURCE_REPOSITORY_INVALID")
        return resolved
    except GitSourceImportError:
        raise
    except (OSError, UnicodeError, TypeError, ValueError):
        _deny("GIT_SOURCE_REPOSITORY_INVALID")


def _load_exact_blob(repo_root: Path, commit_oid: str, path: str) -> tuple[str, str, bytes]:
    object_format = _run_git(repo_root, "rev-parse", "--show-object-format", failure_code="GIT_SOURCE_REPOSITORY_INVALID").decode("ascii", errors="strict").strip()
    lengths = {"sha1": 40, "sha256": 64}
    oid_length = lengths.get(object_format)
    if oid_length is None or len(commit_oid) != oid_length or _OID.fullmatch(commit_oid) is None:
        _deny("GIT_SOURCE_COMMIT_INVALID")
    commit_oid = commit_oid.lower()
    commit_type = _run_git(repo_root, "cat-file", "-t", commit_oid, failure_code="GIT_SOURCE_COMMIT_INVALID").decode("ascii", errors="strict").strip()
    resolved_commit = _run_git(repo_root, "rev-parse", "--verify", "--end-of-options", f"{commit_oid}^{{commit}}", failure_code="GIT_SOURCE_COMMIT_INVALID").decode("ascii", errors="strict").strip()
    if commit_type != "commit" or resolved_commit != commit_oid:
        _deny("GIT_SOURCE_COMMIT_INVALID")
    entries = _run_git(
        repo_root, "--literal-pathspecs", "ls-tree", "-z", "--full-tree", commit_oid, "--", path,
    ).split(b"\x00")
    entries = [entry for entry in entries if entry]
    if not entries:
        _deny("GIT_SOURCE_PATH_INVALID")
    if len(entries) != 1:
        _deny("GIT_SOURCE_ENTRY_UNSUPPORTED")
    metadata, separator, returned_path = entries[0].partition(b"\t")
    try:
        mode, entry_type, blob_oid = metadata.decode("ascii").split(" ")
        expected_path = path.encode("utf-8", errors="strict")
    except (UnicodeError, ValueError):
        _deny("GIT_SOURCE_ENTRY_UNSUPPORTED")
    if not separator or returned_path != expected_path:
        _deny("GIT_SOURCE_PATH_INVALID")
    if mode not in {"100644", "100755"} or entry_type != "blob":
        _deny("GIT_SOURCE_ENTRY_UNSUPPORTED")
    if len(blob_oid) != oid_length or _OID.fullmatch(blob_oid) is None:
        _deny("GIT_SOURCE_OBJECT_UNAVAILABLE")
    actual_type = _run_git(repo_root, "cat-file", "-t", blob_oid).decode("ascii", errors="strict").strip()
    if actual_type != "blob":
        _deny("GIT_SOURCE_ENTRY_UNSUPPORTED")
    blob = _run_git(repo_root, "cat-file", "blob", blob_oid)
    return object_format, blob_oid, blob


def _run_binding(trace, *, task_id: str, run_id: str, grant_id: str) -> dict[str, Any]:
    try:
        run = trace.inspect_run_binding(run_id)
    except TraceAdmissionDenied:
        _deny("GIT_SOURCE_AUTHORITY_DENIED")
    if (
        run["task_id"] != task_id
        or run["grant_id"] != grant_id
        or run["status"] in _TERMINAL_RUN_STATES
    ):
        _deny("GIT_SOURCE_AUTHORITY_DENIED")
    return run


def _require_classification_boundary(run: dict[str, Any], plan: dict[str, Any]) -> None:
    boundary = run["data_boundary"]
    if (
        plan["sensitivity_level"] not in set(boundary.get("allowed_classifications", []))
        or not set(plan["handling_tags"]).issubset(set(boundary.get("handling_tags", [])))
    ):
        _deny("GIT_SOURCE_AUTHORITY_DENIED")


def _authorize_object_write(authority, *, task_id: str, grant_id: str, object_id: str,
                            command_id: str) -> None:
    try:
        authority.evaluate_authorization(
            grant_id,
            {"task": task_id, "resource": object_id, "action": "OBJECT_WRITE", "audience": "nexus-runtime"},
            command_id,
        )
    except (AuthorizationDenied, ApprovalDenied, InvalidDelegation):
        _deny("GIT_SOURCE_AUTHORITY_DENIED")


def _record_object_classification(authority, *, plan: dict[str, Any], assertion_id: str,
                                  object_id: str, command_id: str) -> None:
    try:
        chain = authority.validate_delegation_chain(plan["grant_id"])
        assertion = {
            "schema_id": "nexus.classification_assertion",
            "schema_version": 1,
            "assertion_id": assertion_id,
            "subject_type": "OBJECT",
            "subject_ref": object_id,
            "sensitivity_level": plan["sensitivity_level"],
            "handling_tags": sorted(plan["handling_tags"]),
            "policy_version": authority.policy["policy_version"],
            "reason": "Explicit local Git source import.",
            "actor_id": chain[-1]["granted_to"],
        }
        authority.record_classification_assertion(
            assertion, grant_id=plan["grant_id"], task_id=plan["task_id"],
            audience="nexus-runtime", command_id=command_id,
        )
    except (AuthorizationDenied, ApprovalDenied, InvalidDelegation):
        _deny("GIT_SOURCE_AUTHORITY_DENIED")
    except sqlite3.IntegrityError:
        _deny("GIT_SOURCE_IMPORT_CONFLICT")


def import_git_source(*, store, authority, trace, repository_path: str | os.PathLike[str],
                      plan: dict[str, Any]) -> dict[str, Any]:
    """Import a single commit/path as Artifact + derived provenance Evidence.

    ``repository_path`` is process-local only and is never written to Nexus or
    returned. All durable IDs and command identities are fixed by ``plan``.
    """
    request = _validate_plan(plan)
    repo_root = _validated_repo_root(repository_path)
    try:
        object_format, blob_oid, blob = _load_exact_blob(repo_root, request["commit_oid"], request["path"])
    except GitSourceImportError:
        raise
    except (OSError, UnicodeError, ValueError):
        _deny("GIT_SOURCE_OBJECT_UNAVAILABLE")

    run = _run_binding(
        trace, task_id=request["task_id"], run_id=request["run_id"], grant_id=request["grant_id"],
    )
    _require_classification_boundary(run, request)

    prefix = request["command_id_prefix"]
    artifact_id = request["artifact_object_id"]
    evidence_id = request["evidence_object_id"]
    with store._lock:
        _run_binding(trace, task_id=request["task_id"], run_id=request["run_id"], grant_id=request["grant_id"])
        _authorize_object_write(
            authority, task_id=request["task_id"], grant_id=request["grant_id"],
            object_id=artifact_id, command_id=prefix + ":authorize-artifact",
        )
        _record_object_classification(
            authority, plan=request, assertion_id=request["artifact_classification_assertion_id"],
            object_id=artifact_id, command_id=prefix + ":classify-artifact",
        )
        try:
            store.put_object(
                command_id=prefix + ":artifact", object_id=artifact_id, payload=blob,
                object_type="artifact", created_by_run=request["run_id"],
                classification_assertion_ref=request["artifact_classification_assertion_id"],
            )
        except CommandConflict:
            _deny("GIT_SOURCE_IMPORT_CONFLICT")
        except sqlite3.IntegrityError:
            _deny("GIT_SOURCE_IMPORT_CONFLICT")
        except NexusStoreError:
            _deny("GIT_SOURCE_IMPORT_FAILED")

        try:
            artifact_meta = store.get_object_metadata(artifact_id)
            if artifact_meta.get("payload_state") != "AVAILABLE":
                _deny("GIT_SOURCE_IMPORT_FAILED")
            store.verify_object(artifact_id)
            artifact_sha256 = artifact_meta.get("integrity_hash")
            actual_sha256 = hashlib.sha256(blob).hexdigest()
            if not isinstance(artifact_sha256, str) or artifact_sha256 != actual_sha256:
                _deny("GIT_SOURCE_IMPORT_FAILED")
        except GitSourceImportError:
            raise
        except NexusStoreError:
            _deny("GIT_SOURCE_IMPORT_FAILED")

        evidence = {
            "schema_id": "nexus.git_source_evidence",
            "schema_version": 1,
            "repository_id": request["repository_id"],
            "git_object_format": object_format,
            "commit_oid": request["commit_oid"].lower(),
            "path": request["path"],
            "blob_oid": blob_oid,
            "artifact_ref": artifact_id,
            "artifact_sha256": artifact_sha256,
            "byte_size": len(blob),
            "provenance": "IMPORTED_FROM_PRE_NEXUS_HISTORY",
        }
        try:
            store._validate("nexus.git_source_evidence@1.schema.json", evidence)
        except Exception:
            _deny("GIT_SOURCE_IMPORT_FAILED")

        _run_binding(trace, task_id=request["task_id"], run_id=request["run_id"], grant_id=request["grant_id"])
        _authorize_object_write(
            authority, task_id=request["task_id"], grant_id=request["grant_id"],
            object_id=evidence_id, command_id=prefix + ":authorize-evidence",
        )
        _record_object_classification(
            authority, plan=request, assertion_id=request["evidence_classification_assertion_id"],
            object_id=evidence_id, command_id=prefix + ":classify-evidence",
        )
        try:
            evidence_bytes = json.dumps(
                evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")
            store.put_object(
                command_id=prefix + ":evidence", object_id=evidence_id, payload=evidence_bytes,
                object_type="evidence", created_by_run=request["run_id"],
                classification_assertion_ref=request["evidence_classification_assertion_id"],
                derived_from=[artifact_id],
            )
        except CommandConflict:
            _deny("GIT_SOURCE_IMPORT_CONFLICT")
        except sqlite3.IntegrityError:
            _deny("GIT_SOURCE_IMPORT_CONFLICT")
        except NexusStoreError:
            _deny("GIT_SOURCE_IMPORT_FAILED")

    return {
        "status": "IMPORTED",
        "repository_id": request["repository_id"],
        "commit_oid": request["commit_oid"].lower(),
        "path": request["path"],
        "blob_oid": blob_oid,
        "artifact_ref": artifact_id,
        "evidence_ref": evidence_id,
        "artifact_sha256": artifact_sha256,
        "byte_size": len(blob),
    }
