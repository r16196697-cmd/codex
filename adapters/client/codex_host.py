"""Codex-only event normalization and user-level Hook registration adapter."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import sys
import sysconfig
import tempfile
from typing import Any, Callable

from adapters.client.host_file_lock import HostFileLockError, path_mutation_lock
from adapters.client.host_integration import (
    HostSessionStartInput,
    build_host_context,
    render_host_workspace,
)


CODEX_SESSION_START_SOURCES = ("startup", "resume", "clear", "compact")
_CODEX_SOURCE_MAP = {
    "startup": "NEW_SESSION",
    "resume": "RESUMED_SESSION",
    "clear": "RESET_SESSION",
    "compact": "COMPACTED_SESSION",
}
_EXPECTED_REGISTRATION = {
    "matcher": "startup|resume|clear|compact",
    "hooks": [{
        "type": "command",
        "command": "nexus hook session-start",
        "commandWindows": "nexus hook session-start",
        "timeout": 10,
        "statusMessage": "Loading Nexus continuity",
        "additionalContextLimit": 1200,
    }],
}
_MAX_CONFIG_BYTES = 1_048_576
SESSION_OUTPUT_TOKEN_LIMIT = 1000


class CodexHostError(RuntimeError):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


def codex_launcher_available() -> bool:
    """Recognize either PATH discovery or the installed console script running us."""
    if shutil.which("nexus") is not None or shutil.which("nexus.exe") is not None:
        return True
    # Python's Windows shutil.which may reject an installed .exe because of
    # POSIX-style X_OK checks. Windows command lookup is based on a matching
    # executable in PATH, so check that concrete case directly.
    if os.name == "nt":
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            directory = directory.strip().strip('"')
            if directory and (Path(directory) / "nexus.exe").is_file():
                return True
    for raw_path in (sys.argv[0], sys.executable):
        executable = Path(raw_path)
        if executable.name.casefold() in {"nexus", "nexus.exe"} and executable.is_file():
            return True
    scheme = "nt_user" if os.name == "nt" else "posix_user"
    try:
        installed_script = Path(sysconfig.get_path("scripts", scheme)) / ("nexus.exe" if os.name == "nt" else "nexus")
        if installed_script.is_file():
            return True
    except (KeyError, OSError, TypeError):
        pass
    return False


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def codex_hooks_path(*, environ: dict[str, str] | None = None, home: str | Path | None = None) -> Path:
    try:
        env = os.environ if environ is None else environ
        if "CODEX_HOME" in env:
            selected = env["CODEX_HOME"]
            if not selected:
                raise ValueError
            base = Path(selected).expanduser()
        else:
            base = (Path.home() if home is None else Path(home).expanduser()) / ".codex"
        if not base.is_absolute():
            base = Path.cwd() / base
        return base.resolve(strict=False) / "hooks.json"
    except Exception:
        raise CodexHostError("CODEX_HOOK_CONFIG_UNAVAILABLE") from None


def normalize_session_start(event: Any) -> HostSessionStartInput | None:
    """Normalize Codex's event without passing Codex fields into Nexus state."""
    if not isinstance(event, dict):
        return None
    cwd = event.get("cwd")
    source = event.get("source")
    if not isinstance(cwd, str) or not cwd.strip():
        return None
    if source is not None and source not in _CODEX_SOURCE_MAP:
        return None
    session_id = event.get("session_id")
    if session_id is not None and not isinstance(session_id, str):
        session_id = None
    host_version = event.get("codex_version")
    if host_version is not None and not isinstance(host_version, str):
        host_version = None
    return HostSessionStartInput(
        cwd=cwd,
        host_name="codex",
        host_version=host_version,
        session_id=session_id,
        source=_CODEX_SOURCE_MAP.get(source),
    )


def parse_session_start_json(raw: str) -> dict[str, Any] | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        if len(raw.encode("utf-8", errors="strict")) > 65536:
            return None
        event = json.loads(
            raw,
            object_pairs_hook=_unique_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("invalid JSON constant")),
        )
    except Exception:
        return None
    return event if isinstance(event, dict) else None


def session_start_output(event: Any) -> str:
    """Fail-soft Codex delivery adapter; a missing project emits nothing."""
    request = normalize_session_start(event)
    if request is None:
        return ""
    try:
        context = build_host_context(request)
        return _bound_session_text(render_host_workspace(context))
    except Exception:
        # Host startup must remain available when a local binding is absent or
        # Nexus cannot be read. Never reveal exception text or host paths.
        return ""


def session_text_token_estimate(text: str) -> int:
    ascii_count = sum(character.isascii() for character in text)
    return (ascii_count + 3) // 4 + 2 * (len(text) - ascii_count)


def _bound_session_text(text: str) -> str:
    lines = text.splitlines()
    output = "\n".join(lines)
    while session_text_token_estimate(output) > SESSION_OUTPUT_TOKEN_LIMIT:
        optional = next((index for index in range(len(lines) - 1, 0, -1)
                         if lines[index].startswith("当前约束: ")), None)
        if optional is None:
            optional = next((index for index, line in enumerate(lines)
                             if line.startswith("最近完成: ")), None)
        if optional is not None:
            lines.pop(optional)
        else:
            candidates = [index for index, line in enumerate(lines)
                          if line.startswith(("当前目标: ", "下一步: ")) and len(line) > 48]
            if not candidates:
                return ""
            index = max(candidates, key=lambda item: len(lines[item]))
            lines[index] = lines[index][:-40].rstrip() + "…"
        output = "\n".join(lines)
    return output


class _ConfigRead:
    def __init__(self, document: dict[str, Any], exists: bool):
        self.document = document
        self.exists = exists


def _read_config(path: Path) -> _ConfigRead:
    if path.is_symlink():
        raise CodexHostError("CODEX_HOOK_CONFIG_CONFLICT")
    if not path.exists():
        return _ConfigRead({"hooks": {}}, False)
    try:
        if not path.is_file() or path.stat().st_size > _MAX_CONFIG_BYTES:
            raise ValueError
        raw = path.read_bytes()
        document = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("invalid JSON constant")),
        )
        if not isinstance(document, dict):
            raise ValueError
        hooks = document.get("hooks", {})
        if not isinstance(hooks, dict):
            raise ValueError
        return _ConfigRead(document, True)
    except CodexHostError:
        raise
    except Exception:
        raise CodexHostError("CODEX_HOOK_CONFIG_CONFLICT") from None


def _is_nexus_registration(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    handlers = row.get("hooks", [])
    if not isinstance(handlers, list):
        return False
    for handler in handlers:
        if not isinstance(handler, dict):
            continue
        command = handler.get("command")
        status_message = handler.get("statusMessage")
        if isinstance(command, str) and "nexus" in command.lower():
            return True
        if isinstance(status_message, str) and "nexus" in status_message.lower():
            return True
    return False


def _registration_state(document: dict[str, Any]) -> tuple[str, int | None]:
    hooks = document.get("hooks", {})
    if not isinstance(hooks, dict):
        raise CodexHostError("CODEX_HOOK_CONFIG_CONFLICT")
    candidates: list[tuple[str, int, Any]] = []
    for event_name, rows in hooks.items():
        if not isinstance(rows, list):
            if event_name == "SessionStart":
                raise CodexHostError("CODEX_HOOK_CONFIG_CONFLICT")
            continue
        candidates.extend(
            (event_name, index, row)
            for index, row in enumerate(rows)
            if _is_nexus_registration(row)
        )
    if not candidates:
        return "NOT_INSTALLED", None
    if (len(candidates) != 1 or candidates[0][0] != "SessionStart"
            or candidates[0][2] != _EXPECTED_REGISTRATION):
        return "CONFLICT", None
    return "INSTALLED_TRUST_UNKNOWN", candidates[0][1]


def _result_status(state: str) -> dict[str, Any]:
    return {
        "host": "codex",
        "status": state,
        "trust": "UNKNOWN" if state == "INSTALLED_TRUST_UNKNOWN" else "NOT_OBSERVED",
        "delivery": "USER_LEVEL_SESSION_START_HOOK",
    }


def codex_host_status(*, config_path: str | Path | None = None) -> dict[str, Any]:
    try:
        path = Path(config_path) if config_path is not None else codex_hooks_path()
        read = _read_config(path)
        state, _ = _registration_state(read.document)
        return _result_status(state)
    except CodexHostError as exc:
        state = "CONFLICT" if exc.reason_code == "CODEX_HOOK_CONFIG_CONFLICT" else "UNAVAILABLE"
        return {**_result_status(state), "reason": exc.reason_code}
    except OSError:
        return {**_result_status("UNAVAILABLE"), "reason": "CODEX_HOOK_CONFIG_UNAVAILABLE"}


def _write_atomic(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise CodexHostError("CODEX_HOOK_CONFIG_CONFLICT")
    content = (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
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
    except CodexHostError:
        raise
    except Exception:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise CodexHostError("CODEX_HOOK_CONFIG_WRITE_FAILED") from None


def _mutate(
    *, path: Path, action: str, confirmation: Callable[[str, dict[str, Any]], bool] | None,
) -> dict[str, Any]:
    try:
        initial = _read_config(path)
        state, index = _registration_state(initial.document)
    except CodexHostError:
        raise
    except OSError:
        raise CodexHostError("CODEX_HOOK_CONFIG_UNAVAILABLE") from None

    if action == "install" and state == "INSTALLED_TRUST_UNKNOWN":
        return {"status": "HOST_ADAPTER_ALREADY_INSTALLED", "host": "codex", "replayed": True}
    if action == "uninstall" and state == "NOT_INSTALLED":
        return {"status": "HOST_ADAPTER_NOT_INSTALLED", "host": "codex", "replayed": True}
    if state == "CONFLICT":
        raise CodexHostError("CODEX_HOOK_CONFIG_CONFLICT")
    if action == "install" and not codex_launcher_available():
        raise CodexHostError("NEXUS_LAUNCHER_UNAVAILABLE")

    expected_phrase = (
        "INSTALL NEXUS HOST ADAPTER CODEX" if action == "install"
        else "UNINSTALL NEXUS HOST ADAPTER CODEX"
    )
    summary = {
        "host": "codex",
        "operation": action.upper(),
        "target": "user-level Codex hooks.json",
        "event": "SessionStart",
        "sources": list(CODEX_SESSION_START_SOURCES),
        "command": "nexus hook session-start",
        "read_only": True,
    }
    if confirmation is None or not confirmation(expected_phrase, summary):
        raise CodexHostError("HOST_HOOK_CONFIRMATION_DENIED")

    try:
        with path_mutation_lock(path):
            current = _read_config(path)
            current_state, current_index = _registration_state(current.document)
            if action == "install" and current_state == "INSTALLED_TRUST_UNKNOWN":
                return {"status": "HOST_ADAPTER_ALREADY_INSTALLED", "host": "codex", "replayed": True}
            if action == "uninstall" and current_state == "NOT_INSTALLED":
                return {"status": "HOST_ADAPTER_NOT_INSTALLED", "host": "codex", "replayed": True}
            if current_state == "CONFLICT":
                raise CodexHostError("CODEX_HOOK_CONFIG_CONFLICT")

            updated = copy.deepcopy(current.document)
            hook_map = updated.setdefault("hooks", {})
            if not isinstance(hook_map, dict):
                raise CodexHostError("CODEX_HOOK_CONFIG_CONFLICT")
            rows = hook_map.setdefault("SessionStart", [])
            if not isinstance(rows, list):
                raise CodexHostError("CODEX_HOOK_CONFIG_CONFLICT")
            if action == "install":
                rows.append(copy.deepcopy(_EXPECTED_REGISTRATION))
                status = "HOST_ADAPTER_INSTALLED"
            else:
                if current_index is None:
                    raise CodexHostError("CODEX_HOOK_CONFIG_CONFLICT")
                rows.pop(current_index)
                if not rows:
                    hook_map.pop("SessionStart", None)
                status = "HOST_ADAPTER_UNINSTALLED"
            _write_atomic(path, updated)
            return {"status": status, "host": "codex", "replayed": False}
    except HostFileLockError:
        raise CodexHostError("CODEX_HOOK_CONFIG_LOCK_UNAVAILABLE") from None


def install_codex_host(*, config_path: str | Path | None = None,
                       confirmation: Callable[[str, dict[str, Any]], bool] | None = None) -> dict[str, Any]:
    path = Path(config_path) if config_path is not None else codex_hooks_path()
    return _mutate(path=path, action="install", confirmation=confirmation)


def uninstall_codex_host(*, config_path: str | Path | None = None,
                         confirmation: Callable[[str, dict[str, Any]], bool] | None = None) -> dict[str, Any]:
    path = Path(config_path) if config_path is not None else codex_hooks_path()
    return _mutate(path=path, action="uninstall", confirmation=confirmation)
