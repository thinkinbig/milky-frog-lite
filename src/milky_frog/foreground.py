from __future__ import annotations

from collections.abc import Awaitable
from pathlib import Path
from typing import Protocol

from milky_frog.domain import RunCancellation, RunRequest, RunResult, SteeringChannel
from milky_frog.harness import Harness


class ForegroundRun(Protocol):
    """Awaitable factory wired into :meth:`MilkyFrog._drive` for one foreground Run."""

    def __call__(
        self,
        cancellation: RunCancellation,
        steering: SteeringChannel,
    ) -> Awaitable[RunResult]: ...


class StartRun:
    """Start a fresh Run through the Harness."""

    def __init__(
        self,
        harness: Harness,
        *,
        prompt: str,
        workspace: Path,
        max_model_calls: int,
    ) -> None:
        self._harness = harness
        self._prompt = prompt
        self._workspace = workspace
        self._max_model_calls = max_model_calls

    async def __call__(
        self,
        cancellation: RunCancellation,
        steering: SteeringChannel,
    ) -> RunResult:
        return await self._harness.run(
            RunRequest(
                self._prompt,
                self._workspace,
                max_model_calls=self._max_model_calls,
                cancellation=cancellation,
                steering=steering,
            )
        )


class ResumeRun:
    """Advance an existing Run through the Harness."""

    def __init__(
        self,
        harness: Harness,
        *,
        run_id: str,
        max_model_calls: int,
        prompt: str | None,
    ) -> None:
        self._harness = harness
        self._run_id = run_id
        self._max_model_calls = max_model_calls
        self._prompt = prompt

    async def __call__(
        self,
        cancellation: RunCancellation,
        steering: SteeringChannel,
    ) -> RunResult:
        return await self._harness.resume(
            self._run_id,
            max_model_calls=self._max_model_calls,
            cancellation=cancellation,
            prompt=self._prompt,
            steering=steering,
        )
