from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from milky_frog.checkpoint.events import RunEvent
from milky_frog.domain import RunStatus


@dataclass(frozen=True, slots=True)
class StoredRun:
    run_id: str
    workspace: Path
    status: RunStatus
    created_at: datetime
    updated_at: datetime


class RunClaimError(RuntimeError):
    """A Run is currently owned by another live foreground process."""


class CheckpointStore(Protocol):
    def claim(self, run_id: str) -> AbstractContextManager[None]: ...

    def create_run(self, run_id: str, workspace: Path) -> StoredRun: ...

    def append(self, run_id: str, event: RunEvent, status: RunStatus | None = None) -> RunEvent: ...

    def prepare_resume(
        self,
        run_id: str,
        expected_updated_at: datetime,
        events: tuple[RunEvent, ...] = (),
    ) -> tuple[RunEvent, ...]: ...

    def events(self, run_id: str) -> tuple[RunEvent, ...]: ...

    def get_run(self, run_id: str) -> StoredRun | None: ...

    def list_runs(self, limit: int = 20) -> tuple[StoredRun, ...]: ...
