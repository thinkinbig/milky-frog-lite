from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from milky_frog.domain import RunState, RunStatus


@dataclass(frozen=True, slots=True)
class StoredRun:
    run_id: str
    workspace: Path
    status: RunStatus
    created_at: datetime
    updated_at: datetime
    final_message: str | None = None


class RunClaimError(RuntimeError):
    """A Run is currently owned by another live foreground process."""


class CheckpointStore(Protocol):
    def claim(self, run_id: str) -> AbstractContextManager[None]: ...

    def create_run(self, run_id: str, workspace: Path) -> StoredRun: ...

    def save_state(
        self,
        run_id: str,
        state: RunState,
        *,
        status: RunStatus | None = None,
        final_message: str | None = None,
    ) -> None: ...

    def load_state(self, run_id: str) -> RunState: ...

    def prepare_resume(
        self,
        run_id: str,
        expected_updated_at: datetime,
        state: RunState,
    ) -> StoredRun: ...

    def get_run(self, run_id: str) -> StoredRun | None: ...

    def list_runs(
        self, limit: int = 20, workspace: Path | None = None
    ) -> tuple[StoredRun, ...]: ...

    def resolve_run_id(self, token: str) -> str: ...

    def prune(
        self, before: datetime, workspace: Path | None = None, *, dry_run: bool = False
    ) -> int:
        """Delete terminal Runs older than *before*.

        Pass *workspace* to limit to one Workspace; ``None`` sweeps the whole store.
        Never touches RUNNING, WAITING_FOR_INPUT, or WAITING_FOR_APPROVAL Runs.
        Returns the number of rows affected (or that *would* be affected when *dry_run* is true).
        """
        ...
