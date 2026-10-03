"""Small interprocess lock for host-local configuration files."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
from typing import Iterator


class HostFileLockError(OSError):
    """A path-keyed host configuration lock could not be acquired."""


@contextmanager
def path_mutation_lock(target: str | Path) -> Iterator[None]:
    """Serialize mutations to a host-local file using an OS-released lock.

    The persistent sidecar is only a lock target. The operating-system lock is
    released when its owning process exits, including after ordinary crashes.
    Read-only users do not create or acquire this sidecar.
    """
    target_path = Path(target)
    lock_path = target_path.with_name(target_path.name + ".lock")
    handle = None
    acquired = False
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        if lock_path.is_symlink():
            raise HostFileLockError("HOST_CONFIG_LOCK_UNAVAILABLE")
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(lock_path, flags, 0o600)
        handle = os.fdopen(descriptor, "r+b", buffering=0)
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            os.fsync(handle.fileno())
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        acquired = True
    except HostFileLockError:
        if handle is not None:
            handle.close()
        raise
    except Exception as exc:
        if handle is not None:
            handle.close()
        raise HostFileLockError("HOST_CONFIG_LOCK_UNAVAILABLE") from exc

    try:
        yield
    finally:
        if handle is not None:
            if acquired:
                try:
                    if os.name == "nt":
                        import msvcrt

                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            handle.close()
