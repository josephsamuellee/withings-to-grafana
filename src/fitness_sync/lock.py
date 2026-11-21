"""Exclusive process lock for sync/token refresh."""

from __future__ import annotations

import os
import time
from pathlib import Path


class ProcessLockError(RuntimeError):
    pass


class ProcessLock:
    """Simple exclusive lock using O_EXCL create (portable enough for Pi)."""

    def __init__(self, path: Path, *, stale_after_seconds: int = 3600) -> None:
        self.path = path
        self.stale_after_seconds = stale_after_seconds
        self._held = False

    def acquire(self, *, blocking: bool = False, timeout: float = 0) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout if blocking else time.monotonic()
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                try:
                    os.write(fd, f"{os.getpid()}\n".encode())
                finally:
                    os.close(fd)
                self._held = True
                return
            except FileExistsError:
                if self._is_stale():
                    try:
                        self.path.unlink()
                        continue
                    except OSError:
                        pass
                if not blocking or time.monotonic() >= deadline:
                    raise ProcessLockError(
                        f"another fitness-sync process holds {self.path}"
                    )
                time.sleep(0.2)

    def release(self) -> None:
        if self._held:
            try:
                self.path.unlink(missing_ok=True)
            finally:
                self._held = False

    def _is_stale(self) -> bool:
        try:
            age = time.time() - self.path.stat().st_mtime
            return age > self.stale_after_seconds
        except OSError:
            return False

    def __enter__(self) -> ProcessLock:
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()
