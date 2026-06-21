from __future__ import annotations

from typing import Protocol

from milky_frog.domain import RunResult


class RunAdvancer(Protocol):
    """Advance the interactive loop: start a Run or continue one with a new turn."""

    def __call__(self, task: str, run_id: str | None) -> RunResult: ...


class RunCanceller(Protocol):
    """Request cooperative cancellation of the foreground Run."""

    def __call__(self) -> None: ...
