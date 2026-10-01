"""Portable project identity and host-local Nexus instance location.

The repository manifest identifies a project.  The host registry stores only
where this host has attached an already-existing Nexus instance.  Every locate
revalidates that instance through the reviewed read-only Panel composition.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Callable


_PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MANIFEST_KEYS = {"schema_id", "schema_version", "project_id"}
_REGISTRY_KEYS = {"schema_id", "schema_version", "projects"}
_ENTRY_KEYS = {
    "instance_id", "policy_version", "policy_sha256", "journal_identity",
    "data_root", "policy_path", "independent_purge_journal_path",
}


class ProjectLocatorError(Exception):
    """Stable, sanitized operator-facing failure."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class ProjectManifest:
    project_id: str
    project_root: Path
    manifest_path: Path


def _fail(reason: str) -> None:
    raise ProjectLocatorError(reason)


def _pairs_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("non-standard JSON constant")


def _strict_json(raw: bytes) -> Any:
    return json.loads(
        raw.decode("utf-8", errors="strict"),
        object_pairs_hook=_pairs_no_duplicates,
        parse_constant=_reject_constant,
    )


def _valid_project_id(value: Any) -> bool:
    if not isinstance(value, str) or value in {".", ".."} or "*" in value:
        return False
    if _PROJECT_ID.fullmatch(value) is None:
        return False
    return (
        not Path(value).is_absolute()
        and not PureWindowsPath(value).is_absolute()
        and not PureWindowsPath(value).drive
    )


def _canonical_path(value: str | Path, *, must_exist: bool = False) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        _fail("PROJECT_REGISTRY_INVALID")
    try:
        return path.resolve(strict=must_exist)
    except (OSError, RuntimeError):
        _fail("PROJECT_REGISTRY_INVALID")


def read_project_manifest(path: str | Path) -> ProjectManifest:
    manifest_path = Path(path)
    if manifest_path.name != "project.json" or manifest_path.parent.name != ".nexus":
        _fail("PROJECT_MANIFEST_INVALID")
    if manifest_path.parent.is_symlink() or manifest_path.is_symlink() or not manifest_path.is_file():
        _fail("PROJECT_MANIFEST_INVALID")
    try:
        document = _strict_json(manifest_path.read_bytes())
    except Exception:
        _fail("PROJECT_MANIFEST_INVALID")
    if (
        not isinstance(document, dict)
        or set(document) != _MANIFEST_KEYS
        or document.get("schema_id") != "nexus.project_manifest"
        or type(document.get("schema_version")) is not int
        or document.get("schema_version") != 1
        or not _valid_project_id(document.get("project_id"))
    ):
        _fail("PROJECT_MANIFEST_INVALID")
    return ProjectManifest(
        project_id=document["project_id"],
        project_root=manifest_path.parent.parent.resolve(),
        manifest_path=manifest_path.resolve(),
    )


def find_project_manifest(start_dir: str | Path | None = None) -> ProjectManifest:
    """Walk only the starting directory's ancestors; nearest manifest wins."""
    try:
        start = Path.cwd() if start_dir is None else Path(start_dir).expanduser()
        start = start.resolve(strict=True)
    except (OSError, RuntimeError):
        _fail("PROJECT_NOT_ATTACHED")
    if start.is_file():
        start = start.parent
    for directory in (start, *start.parents):
        manifest = directory / ".nexus" / "project.json"
        if manifest.is_symlink() or manifest.exists():
            return read_project_manifest(manifest)
    _fail("PROJECT_NOT_ATTACHED")


def default_project_registry_path(
    *, platform: str | None = None, environ: dict[str, str] | None = None,
    home: str | Path | None = None,
) -> Path:
    env = os.environ if environ is None else environ
    selected_platform = os.name if platform is None else platform
    if selected_platform == "nt":
        local_app_data = env.get("LOCALAPPDATA")
        if not local_app_data:
            _fail("PROJECT_REGISTRY_UNAVAILABLE")
        base = _canonical_path(local_app_data)
        return base / "Nexus" / "projects-v1.json"
    xdg_state = env.get("XDG_STATE_HOME")
    if xdg_state:
        base = _canonical_path(xdg_state)
    else:
        selected_home = Path.home() if home is None else Path(home).expanduser()
        base = _canonical_path(selected_home) / ".local" / "state"
    return base / "nexus" / "projects-v1.json"


def resolve_project_registry_path(
    registry_path: str | Path | None = None, *,
    environ: dict[str, str] | None = None,
) -> Path:
    if registry_path is not None:
        return _canonical_path(registry_path)
    env = os.environ if environ is None else environ
    if "NEXUS_PROJECT_REGISTRY" in env:
        value = env["NEXUS_PROJECT_REGISTRY"]
        if not value:
            _fail("PROJECT_REGISTRY_INVALID")
        return _canonical_path(value)
    return default_project_registry_path(environ=env)


def _canonical_json(document: dict[str, Any]) -> bytes:
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _read_registry_document(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        _fail("PROJECT_REGISTRY_INVALID")
    if not path.exists():
        return {"schema_id": "nexus.host_project_registry", "schema_version": 1, "projects": {}}
    if not path.is_file():
        _fail("PROJECT_REGISTRY_INVALID")
    try:
        document = _strict_json(path.read_bytes())
    except Exception:
        _fail("PROJECT_REGISTRY_INVALID")
    if (
        not isinstance(document, dict)
        or set(document) != _REGISTRY_KEYS
        or document.get("schema_id") != "nexus.host_project_registry"
        or type(document.get("schema_version")) is not int
        or document.get("schema_version") != 1
        or not isinstance(document.get("projects"), dict)
    ):
        _fail("PROJECT_REGISTRY_INVALID")
    for project_id, entry in document["projects"].items():
        if not _valid_project_id(project_id) or not isinstance(entry, dict) or set(entry) != _ENTRY_KEYS:
            _fail("PROJECT_REGISTRY_INVALID")
        for key in ("instance_id", "policy_version"):
            if not isinstance(entry[key], str) or not entry[key]:
                _fail("PROJECT_REGISTRY_INVALID")
        for key in ("policy_sha256", "journal_identity"):
            value = entry[key]
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                _fail("PROJECT_REGISTRY_INVALID")
        for key in ("data_root", "independent_purge_journal_path"):
            value = entry[key]
            if not isinstance(value, str):
                _fail("PROJECT_REGISTRY_INVALID")
            try:
                normalized = _canonical_path(value)
            except ProjectLocatorError:
                _fail("PROJECT_REGISTRY_INVALID")
            if str(normalized) != value:
                _fail("PROJECT_REGISTRY_INVALID")
        policy_path = entry["policy_path"]
        if policy_path is not None:
            if not isinstance(policy_path, str):
                _fail("PROJECT_REGISTRY_INVALID")
            try:
                normalized = _canonical_path(policy_path)
            except ProjectLocatorError:
                _fail("PROJECT_REGISTRY_INVALID")
            if str(normalized) != policy_path:
                _fail("PROJECT_REGISTRY_INVALID")
    return document


def read_host_registry(
    registry_path: str | Path | None = None, *, environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    path = resolve_project_registry_path(registry_path, environ=environ)
    return _read_registry_document(path)


def _existing_path(value: str | Path, reason: str) -> Path:
    try:
        path = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        _fail(reason)
    return path


def _verified_binding(
    *, data_root: str | Path, policy_path: str | Path | None,
    journal_path: str | Path, opener=None,
) -> dict[str, Any]:
    root = _existing_path(data_root, "PROJECT_DATA_ROOT_UNAVAILABLE")
    if not root.is_dir() or not (root / "nexus.sqlite").is_file():
        _fail("PROJECT_DATA_ROOT_UNAVAILABLE")
    journal = _existing_path(journal_path, "PROJECT_INSTANCE_BINDING_MISMATCH")
    if not journal.is_file():
        _fail("PROJECT_INSTANCE_BINDING_MISMATCH")
    policy = None
    if policy_path is not None:
        policy = _existing_path(policy_path, "PROJECT_INSTANCE_BINDING_MISMATCH")
        if not policy.is_file():
            _fail("PROJECT_INSTANCE_BINDING_MISMATCH")
    if opener is None:
        from adapters.panel.application import open_panel_application
        opener = open_panel_application
    try:
        app = opener(
            root, policy_path=policy,
            independent_purge_journal_path=journal, read_only=True,
        )
        try:
            binding = app.store.get_instance_binding_status()
            if (
                binding.get("state") not in {"FRESH_BOUND_INSTANCE", "LEGACY_ADOPTED_BOUND_INSTANCE"}
                or binding.get("policy_content_binding") != "BOUND"
            ):
                _fail("PROJECT_INSTANCE_BINDING_MISMATCH")
            return {
                "instance_id": binding["instance_id"],
                "policy_version": binding["policy_version"],
                "policy_sha256": binding["policy_sha256"],
                "journal_identity": binding["journal_identity"],
                "data_root": str(root),
                "policy_path": str(policy) if policy is not None else None,
                "independent_purge_journal_path": str(journal),
            }
        finally:
            app.close()
    except ProjectLocatorError:
        raise
    except Exception as exc:
        reason = getattr(exc, "reason_code", None)
        args = getattr(exc, "args", ())
        reason = reason or (args[0] if args else None)
        if reason in {"NEXUS_WRITER_ALREADY_RUNNING", "NEXUS_WRITER_LOCK_UNAVAILABLE"}:
            _fail("PROJECT_INSTANCE_UNAVAILABLE")
        if reason in {"PURGE_JOURNAL_UNAVAILABLE", "PURGE_JOURNAL_MISSING", "PURGE_JOURNAL_IDENTITY_MISMATCH"}:
            _fail("PROJECT_INSTANCE_BINDING_MISMATCH")
        if reason in {"POLICY_BINDING_MISMATCH", "POLICY_ROTATION_UNSUPPORTED", "INSTANCE_BINDING_REQUEST_INTEGRITY_FAILED"}:
            _fail("PROJECT_INSTANCE_BINDING_MISMATCH")
        _fail("PROJECT_INSTANCE_UNAVAILABLE")


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        _fail("PROJECT_ATTACHMENT_CONFLICT")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except ProjectLocatorError:
        raise
    except Exception:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        _fail("PROJECT_REGISTRY_WRITE_FAILED")


def _manifest_document(project_id: str) -> dict[str, Any]:
    return {"schema_id": "nexus.project_manifest", "schema_version": 1, "project_id": project_id}


def _attachment_record(binding: dict[str, Any]) -> dict[str, Any]:
    return {key: binding[key] for key in sorted(_ENTRY_KEYS)}


def _read_existing_manifest(project_root: Path) -> ProjectManifest | None:
    nexus_dir = project_root / ".nexus"
    if nexus_dir.is_symlink():
        _fail("PROJECT_MANIFEST_INVALID")
    if nexus_dir.exists() and not nexus_dir.is_dir():
        _fail("PROJECT_MANIFEST_INVALID")
    manifest_path = nexus_dir / "project.json"
    if manifest_path.is_symlink():
        _fail("PROJECT_MANIFEST_INVALID")
    if not manifest_path.exists():
        return None
    return read_project_manifest(manifest_path)


def attach_project(
    *, project_root: str | Path, project_id: str | None,
    data_root: str | Path, policy_path: str | Path | None,
    independent_purge_journal_path: str | Path,
    registry_path: str | Path | None = None,
    confirmation: Callable[[str, dict[str, Any]], bool] | None = None,
    opener=None,
) -> dict[str, Any]:
    """Read-only verify, explicitly confirm, then attach manifest and registry."""
    try:
        root = _existing_path(project_root, "PROJECT_NOT_ATTACHED")
    except ProjectLocatorError:
        raise
    if not root.is_dir():
        _fail("PROJECT_NOT_ATTACHED")
    existing_manifest = _read_existing_manifest(root)
    selected_id = project_id or (existing_manifest.project_id if existing_manifest else None)
    if not _valid_project_id(selected_id):
        _fail("PROJECT_MANIFEST_INVALID")
    if existing_manifest is not None and existing_manifest.project_id != selected_id:
        _fail("PROJECT_ALREADY_ATTACHED_DIFFERENT_PROJECT")

    registry_file = resolve_project_registry_path(registry_path)
    registry = _read_registry_document(registry_file)
    binding = _verified_binding(
        data_root=data_root, policy_path=policy_path,
        journal_path=independent_purge_journal_path, opener=opener,
    )
    record = _attachment_record(binding)
    prior = registry["projects"].get(selected_id)
    if prior is not None and prior != record:
        _fail("PROJECT_ATTACHMENT_CONFLICT")
    exact_replay = existing_manifest is not None and prior == record
    if exact_replay:
        return {"status": "PROJECT_ALREADY_ATTACHED", "project_id": selected_id, **record}

    summary = {
        "project_id": selected_id,
        "project_root": str(root),
        "instance_id": binding["instance_id"],
        "data_root": binding["data_root"],
        "policy_version": binding["policy_version"],
        "policy_sha256": binding["policy_sha256"],
        "journal_identity": binding["journal_identity"],
    }
    expected = f"ATTACH {selected_id}"
    if confirmation is None:
        _fail("INTERACTIVE_TTY_REQUIRED")
    if not confirmation(expected, summary):
        _fail("PROJECT_ATTACH_CONFIRMATION_DENIED")

    # Re-check all conflict and binding conditions after HUMAN confirmation.
    existing_manifest = _read_existing_manifest(root)
    if existing_manifest is not None and existing_manifest.project_id != selected_id:
        _fail("PROJECT_ALREADY_ATTACHED_DIFFERENT_PROJECT")
    registry = _read_registry_document(registry_file)
    prior = registry["projects"].get(selected_id)
    if prior is not None and prior != record:
        _fail("PROJECT_ATTACHMENT_CONFLICT")
    binding_after_confirmation = _verified_binding(
        data_root=data_root, policy_path=policy_path,
        journal_path=independent_purge_journal_path, opener=opener,
    )
    if binding_after_confirmation != binding:
        _fail("PROJECT_INSTANCE_BINDING_MISMATCH")
    if existing_manifest is not None and prior == record:
        return {"status": "PROJECT_ALREADY_ATTACHED", "project_id": selected_id, **record}

    manifest_path = root / ".nexus" / "project.json"
    manifest_bytes = _canonical_json(_manifest_document(selected_id))
    if existing_manifest is None:
        _write_atomic(manifest_path, manifest_bytes)
    registry["projects"][selected_id] = record
    registry_bytes = _canonical_json(registry)
    if not registry_file.exists() or registry_file.read_bytes() != registry_bytes:
        _write_atomic(registry_file, registry_bytes)
    return {"status": "PROJECT_ATTACHED", "project_id": selected_id, **record}


def locate_project(
    *, start_dir: str | Path | None = None, registry_path: str | Path | None = None,
    opener=None,
) -> dict[str, Any]:
    manifest = find_project_manifest(start_dir)
    registry_file = resolve_project_registry_path(registry_path)
    registry = _read_registry_document(registry_file)
    record = registry["projects"].get(manifest.project_id)
    if record is None:
        _fail("PROJECT_HOST_BINDING_MISSING")
    actual = _verified_binding(
        data_root=record["data_root"], policy_path=record["policy_path"],
        journal_path=record["independent_purge_journal_path"], opener=opener,
    )
    if actual != record:
        _fail("PROJECT_INSTANCE_BINDING_MISMATCH")
    return {
        "status": "PROJECT_LOCATED",
        "project_id": manifest.project_id,
        "project_root": str(manifest.project_root),
        **record,
    }


def resolve_project_runtime(
    *, start_dir: str | Path | None = None, registry_path: str | Path | None = None,
    opener=None,
) -> dict[str, Any]:
    """Resolve a complete, verified host binding for one CLI invocation."""
    return locate_project(start_dir=start_dir, registry_path=registry_path, opener=opener)
