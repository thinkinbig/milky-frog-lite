from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path

from milky_frog.checkpoint.base import RunClaimError


class RunLock:
    """Crash-safe OS-level advisory lock for a single Run.

    Built on ``flock`` (POSIX) or ``msvcrt.locking`` (Windows). The lock is
    released automatically when the owning process exits, which makes an
    orphaned Run resumable without stale-lock cleanup.
    """

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._directory.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def claim(self, run_id: str) -> Iterator[None]:
        name = sha256(run_id.encode()).hexdigest()
        path = self._directory / name
        with path.open("a+b") as handle:
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            try:
                self._acquire(handle.fileno())
            except OSError as error:
                raise RunClaimError(f"Run {run_id} is already active") from error
            try:
                yield
            finally:
                self._release(handle.fileno())

    @staticmethod
    def _acquire(fd: int) -> None:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            return
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _release(fd: int) -> None:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
            return
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)
