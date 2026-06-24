from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from milky_frog.checkpoint import SqliteCheckpointStore, StoredRun
from milky_frog.checkpoint.snapshot import dump_run_state
from milky_frog.diagnostics import CheckStatus, Diagnostic
from milky_frog.domain import RunState
from milky_frog.project import (
    CONFIG_FILENAME,
    CONFIG_TEMPLATE,
    PROJECT_DIRNAME,
    load_project_config,
)
from milky_frog.settings import Settings


@dataclass(frozen=True, slots=True)
class WorkspaceInitResult:
    root: Path
    already_exists: bool


@dataclass(frozen=True, slots=True)
class RunView:
    run: StoredRun
    state: RunState

    def to_json(self) -> str:
        return json.dumps(
            {
                "run_id": self.run.run_id,
                "status": self.run.status,
                "workspace": str(self.run.workspace),
                "final_message": self.run.final_message,
                "state": json.loads(dump_run_state(self.state)),
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True, slots=True)
class PruneResult:
    count: int
    retention_days: int
    dry_run: bool


def build_doctor_diagnostics(settings: Settings) -> tuple[Diagnostic, ...]:
    return (
        Diagnostic("State directory", CheckStatus.PASS, str(settings.home)),
        Diagnostic(
            "API key",
            CheckStatus.PASS if settings.api_key else CheckStatus.FAIL,
            "configured" if settings.api_key else "missing (MILKY_FROG_API_KEY)",
        ),
        Diagnostic(
            "Base URL",
            CheckStatus.PASS if settings.base_url else CheckStatus.WARN,
            settings.base_url or "provider default",
        ),
        Diagnostic(
            "Model",
            CheckStatus.PASS if settings.model else CheckStatus.FAIL,
            settings.model or "missing (MILKY_FROG_MODEL)",
        ),
    )


def initialize_workspace(workspace: Path | None) -> WorkspaceInitResult:
    root = (workspace or Path.cwd()).expanduser().resolve() / PROJECT_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    (root / "skills").mkdir(exist_ok=True)
    config = root / CONFIG_FILENAME
    if config.exists():
        return WorkspaceInitResult(root=root, already_exists=True)
    config.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    return WorkspaceInitResult(root=root, already_exists=False)


def load_run_view(settings: Settings, run_id: str) -> RunView:
    store = SqliteCheckpointStore(settings.database_path)
    run = store.get_run(run_id)
    if run is None:
        raise LookupError(run_id)
    return RunView(run=run, state=store.load_state(run_id))


def prune_runs(
    settings: Settings,
    workspace: Path,
    *,
    dry_run: bool,
    days: int | None,
) -> PruneResult:
    store = SqliteCheckpointStore(settings.database_path)
    project_cfg = load_project_config(workspace)
    retention = days if days is not None else project_cfg.checkpoint_retention_days
    if retention < 1:
        raise ValueError("retention period must be at least 1 day")
    cutoff = datetime.now(UTC) - timedelta(days=retention)
    count = store.prune(cutoff, workspace, dry_run=dry_run)
    return PruneResult(count=count, retention_days=retention, dry_run=dry_run)
