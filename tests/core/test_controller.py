from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from milky_frog.checkpoint import StoredRun
from milky_frog.core.controller import RunController
from milky_frog.domain import RunStatus


class CheckpointListing:
    def __init__(self, runs: tuple[StoredRun, ...]) -> None:
        self._runs = runs

    def list_runs(self, *, limit: int = 20, workspace: Path | None = None) -> tuple[StoredRun, ...]:
        del limit
        if workspace is None:
            return self._runs
        return tuple(run for run in self._runs if run.workspace == workspace.resolve())


def test_workspace_runs_filters_runs_and_preserves_checkpoint_order(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    other_workspace = tmp_path / "other-workspace"
    moment = datetime.now(UTC)
    recent = StoredRun("recent", workspace, RunStatus.COMPLETED, moment, moment, "latest")
    other = StoredRun("other", other_workspace, RunStatus.COMPLETED, moment, moment, "outside")
    older = StoredRun("older", workspace, RunStatus.CANCELLED, moment, moment, "previous")

    controller = RunController(CheckpointListing((recent, other, older)))  # type: ignore[arg-type]

    assert controller.workspace_runs(workspace) == (recent, older)
