from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from contextlib import contextmanager
from typing import Any


def _canon(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}


def _thread_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path))
    with _LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


class IndependentPurgeJournal:
    """Hash-chained append-only JSONL journal stored outside the database backup root."""

    def __init__(self, path: str | Path, data_root: str | Path):
        self.path = Path(path).expanduser().resolve()
        root = Path(data_root).expanduser().resolve()
        if self.path == root or root in self.path.parents:
            raise ValueError("purge journal must be outside the Nexus data root")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _exclusive_path_lock(self):
        """Serialize journal readers/writers by path, including other processes."""
        lock_path = self.path.with_name(self.path.name + ".lock")
        process_lock = _thread_lock(lock_path)
        with process_lock:
            with lock_path.open("a+b") as handle:
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                locked = False
                try:
                    if os.name == "nt":
                        import msvcrt

                        while not locked:
                            try:
                                handle.seek(0)
                                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                                locked = True
                            except OSError:
                                time.sleep(0.05)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                        locked = True
                    yield
                finally:
                    if locked:
                        handle.seek(0)
                        if os.name == "nt":
                            import msvcrt

                            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl

                            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def read(self) -> list[dict[str, Any]]:
        with self._exclusive_path_lock():
            return self._read_unlocked()

    def _read_unlocked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records = []
        previous = "0" * 64
        for number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            try:
                row = json.loads(line)
            except Exception as exc:
                raise ValueError(f"purge journal corrupt at line {number}") from exc
            digest = row.pop("record_hash", None)
            if row.get("previous_hash") != previous or hashlib.sha256(_canon(row).encode("utf-8")).hexdigest() != digest:
                raise ValueError(f"purge journal hash chain invalid at line {number}")
            if row.get("sequence") != number or row.get("action") not in {"BARRIER_INSTALLED", "BARRIER_PARTIAL", "BARRIER_RELEASED"}:
                raise ValueError(f"purge journal record invalid at line {number}")
            if not isinstance(row.get("protected_refs"), list) or not row.get("barrier_id") or not row.get("plan_id"):
                raise ValueError(f"purge journal record invalid at line {number}")
            row["record_hash"] = digest
            records.append(row)
            previous = digest
        return records

    def _create_empty_journal_durably(self) -> None:
        """Create the pathname with platform-appropriate namespace durability."""
        if os.name == "nt":
            # Windows does not expose POSIX directory fsync through Python's
            # os APIs. Create a sibling and publish its name with the documented
            # MOVEFILE_WRITE_THROUGH contract instead of claiming directory fsync.
            import ctypes

            fd, temporary_name = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.flush()
                    os.fsync(handle.fileno())
                move_file = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
                move_file.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
                move_file.restype = ctypes.c_int
                if not move_file(str(temporary), str(self.path), 0x00000008):  # MOVEFILE_WRITE_THROUGH
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                if temporary.exists():
                    temporary.unlink()
            return

        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)

    def _fsync_posix_parent_directory(self) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_fd = os.open(self.path.parent, flags)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def append(self, *, action: str, barrier_id: str, plan_id: str, plan_hash: str, lineage_revision: int, protected_refs: list[str]) -> dict[str, Any]:
        if action not in {"BARRIER_INSTALLED", "BARRIER_PARTIAL", "BARRIER_RELEASED"}:
            raise ValueError("unsupported purge journal action")
        if lineage_revision < 0 or len(plan_hash) != 64:
            raise ValueError("invalid purge journal plan binding")
        with self._exclusive_path_lock():
            created_here = not self.path.exists()
            prior = self._read_unlocked()
            if created_here:
                self._create_empty_journal_durably()
            body = {"version": 1, "sequence": len(prior) + 1, "action": action, "barrier_id": barrier_id, "plan_id": plan_id, "plan_hash": plan_hash, "lineage_revision": lineage_revision, "protected_refs": sorted(set(protected_refs)), "previous_hash": prior[-1]["record_hash"] if prior else "0" * 64}
            body["record_hash"] = hashlib.sha256(_canon(body).encode("utf-8")).hexdigest()
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(_canon(body) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            if created_here and os.name != "nt":
                self._fsync_posix_parent_directory()
            return body
