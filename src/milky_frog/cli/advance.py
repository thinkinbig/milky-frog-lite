from __future__ import annotations

from pathlib import Path

from milky_frog.domain import RunResult
from milky_frog.runtime import MilkyFrog


class MilkyFrogAdvancer:
    """Interactive-loop adapter over :class:`MilkyFrog` run and resume."""

    def __init__(
        self,
        frog: MilkyFrog,
        workspace: Path,
        *,
        stdin_steering: bool = False,
    ) -> None:
        self._frog = frog
        self._workspace = workspace
        self._stdin_steering = stdin_steering

    def __call__(self, task: str, run_id: str | None) -> RunResult:
        if run_id is None:
            return self._frog.run(
                task, self._workspace, stdin_steering=self._stdin_steering
            )
        return self._frog.resume(run_id, task, stdin_steering=self._stdin_steering)
