from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from pydantic import JsonValue

from milky_frog.domain import RunStatus


@dataclass(frozen=True, slots=True)
class RunEvent:
    event_type: str
    payload: dict[str, JsonValue]
    version: int = 1
    sequence: int | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class StoredRun:
    run_id: str
    workspace: Path
    status: RunStatus
    created_at: datetime
    updated_at: datetime


class CheckpointStore(Protocol):
    def create_run(self, run_id: str, workspace: Path) -> StoredRun: ...

    def append(self, run_id: str, event: RunEvent, status: RunStatus | None = None) -> RunEvent: ...

    def events(self, run_id: str) -> tuple[RunEvent, ...]: ...

    def get_run(self, run_id: str) -> StoredRun | None: ...

    def list_runs(self, limit: int = 20) -> tuple[StoredRun, ...]: ...
