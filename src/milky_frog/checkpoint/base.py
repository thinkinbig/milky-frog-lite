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


@dataclass(frozen=True, slots=True)
class CleanupScope:
    """Explicit target for a scope-sensitive checkpoint maintenance operation.

    The checkpoint store is shared by every Workspace under ``MILKY_FROG_HOME``,
    but retention/prune policy is read from the *current* Workspace's config.
    Requiring this object at every cleanup call forces the caller to state, in
    one place, whether the operation touches a single Workspace or deliberately
    sweeps the whole store — so a workspace-local policy can never silently fan
    out across other Workspaces' Runs. ``None`` workspace means a deliberate
    global sweep, and is only reachable through :meth:`all_workspaces`.
    """

    workspace: Path | None

    @classmethod
    def for_workspace(cls, workspace: Path) -> CleanupScope:
        """Limit the operation to a single Workspace."""
        return cls(workspace=workspace.resolve())

    @classmethod
    def all_workspaces(cls) -> CleanupScope:
        """Deliberately sweep every Workspace in the shared store."""
        return cls(workspace=None)

    @property
    def is_global(self) -> bool:
        return self.workspace is None


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

    def list_runs(self, limit: int = 20) -> tuple[StoredRun, ...]: ...

    def reap_orphans(self, scope: CleanupScope) -> int:
        """Detect and seal Runs whose process died while marked RUNNING.

        Tries to ``claim()`` every in-*scope* RUNNING Run. If the claim succeeds
        the owning process is gone — seal the state and mark the Run CANCELLED.
        Returns the count of orphans recovered. *scope* is required so a
        Workspace-local startup can never reap another Workspace's Runs by
        accident; pass :meth:`CleanupScope.all_workspaces` for a global sweep.
        """
        ...

    def prune(self, before: datetime, scope: CleanupScope, *, dry_run: bool = False) -> int:
        """Delete checkpoint snapshots older than *before* within *scope*.

        Never touches RUNNING, WAITING_FOR_INPUT, or WAITING_FOR_APPROVAL Runs.
        *scope* is required so a Workspace-local retention policy can never
        delete another Workspace's Runs by accident; pass
        :meth:`CleanupScope.all_workspaces` for a deliberate global sweep.
        Returns the number of rows affected (or that *would* be affected when
        *dry_run* is true).
        """
        ...
