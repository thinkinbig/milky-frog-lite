from __future__ import annotations

from pathlib import Path

from milky_frog.domain import ResumeError
from milky_frog.infra.checkpoint.sqlite import SqliteCheckpointStore
from milky_frog.settings import Settings


def find_last_run(store: SqliteCheckpointStore, workspace: Path) -> str | None:
    resolved_workspace = workspace.resolve()
    for run in store.list_runs(limit=20):
        if run.workspace.resolve() == resolved_workspace:
            return run.run_id
    return None


def resolve_run_id(settings: Settings, run_id: str) -> str:
    store = SqliteCheckpointStore(settings.database_path)
    try:
        return store.resolve_run_id(run_id)
    except LookupError as error:
        raise ResumeError(f"unknown Run: {run_id}") from error
    except ValueError as error:
        raise ResumeError(f"ambiguous Run prefix: {run_id}") from error
