"""Small durable, path-free bootstrap intent files."""

from __future__ import annotations

import ctypes
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from kernel.instance_binding import canonical_json_bytes


MAX_INTENT_BYTES = 32 * 1024


def strict_json_object(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_INTENT_BYTES:
        raise ValueError("BOOTSTRAP_INTENT_TOO_LARGE")

    def pairs_hook(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("BOOTSTRAP_INTENT_DUPLICATE_KEY")
            value[key] = item
        return value

    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs_hook)
    if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
        raise ValueError("BOOTSTRAP_INTENT_NOT_CANONICAL")
    return value


def read_intent(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return strict_json_object(path.read_bytes())


def create_durably(path: Path, value: dict[str, Any]) -> None:
    """Atomically publish an immutable intent without replacing an existing one."""
    payload = canonical_json_bytes(value)
    if len(payload) > MAX_INTENT_BYTES:
        raise ValueError("BOOTSTRAP_INTENT_TOO_LARGE")
    fd, temporary_name = tempfile.mkstemp(prefix=".nexus-intent-", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "nt":
            move_file = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
            move_file.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
            move_file.restype = ctypes.c_int
            if not move_file(str(temporary), str(path), 0x00000008):  # MOVEFILE_WRITE_THROUGH; no replace
                raise ctypes.WinError(ctypes.get_last_error())
        else:
            os.link(temporary, path)
            temporary.unlink()
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_fd = os.open(path.parent, flags)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def persist_exact(path: Path, value: dict[str, Any], *, request_field: str = "request_sha256") -> dict[str, Any]:
    prior = read_intent(path)
    if prior is None:
        create_durably(path, value)
        return value
    if prior.get(request_field) != value.get(request_field) or prior != value:
        raise ValueError("BOOTSTRAP_INTENT_CONFLICT")
    return prior
