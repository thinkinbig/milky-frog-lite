from __future__ import annotations

from pathlib import Path

from milky_frog.domain import RunResult
from milky_frog.runtime import MilkyFrog


class MilkyFrogAdvancer:
    """Interactive-loop adapter over :class:`MilkyFrog` run and resume."""

    def __init__(self, frog: MilkyFrog, workspace: Path) -> None:
        self._frog = frog
        self._workspace = workspace

    def __call__(self, task: str, run_id: str | None) -> RunResult:
        if run_id is None:
            return self._frog.run(task, self._workspace)
        return self._frog.resume(run_id, task)
