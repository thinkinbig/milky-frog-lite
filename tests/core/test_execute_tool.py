from __future__ import annotations

import pytest

from milky_frog.core.runtime.execute_tool import run_cancellable
from milky_frog.domain import RunCancellation, ToolRunCancelled


async def finished() -> str:
    return "done"


@pytest.mark.asyncio
async def test_run_cancellable_keeps_completed_work_over_a_simultaneous_cancel() -> None:
    """Work that already finished survives cancellation landing in the same iteration.

    ``asyncio.wait(FIRST_COMPLETED)`` can report the work task and the
    cancellation poll as done together. For a Tool that tie must go to the
    result, since the Tool already ran and its side effects are on disk.
    """
    cancellation = RunCancellation()
    cancellation.cancel()

    assert await run_cancellable(finished(), cancellation, keep_completed_work=True) == "done"


@pytest.mark.asyncio
async def test_run_cancellable_lets_cancellation_win_by_default() -> None:
    """A model turn honors the interrupt rather than keeping a response with no side effects."""
    cancellation = RunCancellation()
    cancellation.cancel()

    with pytest.raises(ToolRunCancelled):
        await run_cancellable(finished(), cancellation)
