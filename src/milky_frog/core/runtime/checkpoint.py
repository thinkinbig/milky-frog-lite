from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path

from milky_frog.checkpoint import CheckpointStore, StoredRun
from milky_frog.domain import RunState, RunStatus
from milky_frog.harness.state import seal


class RunCheckpointFacade:
    """Single access point for Checkpoint operations across Harness, Handlers, and Session.

    Wraps ``CheckpointStore`` so callers do not scatter ``save_state`` / ``claim`` /
    ``prepare_resume`` across three modules.
    """

    def __init__(self, store: CheckpointStore) -> None:
        self._store = store

    @property
    def store(self) -> CheckpointStore:
        return self._store

    def claim(self, run_id: str) -> AbstractContextManager[None]:
        return self._store.claim(run_id)

    def create_run(self, run_id: str, workspace: Path) -> None:
        self._store.create_run(run_id, workspace)

    def get_run(self, run_id: str) -> StoredRun | None:
        return self._store.get_run(run_id)

    def load_state(self, run_id: str) -> RunState:
        return self._store.load_state(run_id)

    def prepare_resume(self, run_id: str, expected_updated_at: datetime, state: RunState) -> None:
        self._store.prepare_resume(run_id, expected_updated_at, state)

    def save_state(
        self,
        run_id: str,
        state: RunState,
        *,
        status: RunStatus,
        final_message: str = "",
    ) -> None:
        self._store.save_state(run_id, state, status=status, final_message=final_message)

    def resolve_run_id(self, run_id: str) -> str:
        return self._store.resolve_run_id(run_id)

    def list_runs(self, *, limit: int = 20, workspace: Path | None = None) -> tuple[StoredRun, ...]:
        return self._store.list_runs(limit=limit, workspace=workspace)

    def prune(self, before: datetime, workspace: Path | None = None) -> int:
        return self._store.prune(before, workspace)

    def seal_interrupt(self, run_id: str, *, reason: str = "interrupted") -> None:
        """Seal an in-flight Run and persist as ``CANCELLED``."""
        stored = self._store.get_run(run_id)
        if stored is None or stored.status is not RunStatus.RUNNING:
            return
        state = self._store.load_state(run_id)
        sealed, _ = seal(state)
        self._store.save_state(
            run_id,
            sealed,
            status=RunStatus.CANCELLED,
            final_message=reason,
        )
