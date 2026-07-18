from __future__ import annotations

import asyncio

import pytest

from milky_frog.core.cleanup import complete_cleanup


@pytest.mark.asyncio
async def test_complete_cleanup_finishes_after_repeated_cancellation() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    finished = False

    async def cleanup() -> None:
        nonlocal finished
        started.set()
        await release.wait()
        finished = True

    task = asyncio.create_task(complete_cleanup(cleanup(), propagate_cancellation=True))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert task.done() is False
    task.cancel()
    await asyncio.sleep(0)
    assert task.done() is False
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished is True


@pytest.mark.asyncio
async def test_complete_cleanup_can_preserve_an_exception_already_in_flight() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    finished = False

    async def cleanup() -> None:
        nonlocal finished
        started.set()
        await release.wait()
        finished = True

    async def fail_then_cleanup() -> None:
        try:
            raise RuntimeError("original failure")
        except RuntimeError:
            await complete_cleanup(cleanup(), propagate_cancellation=False)
            raise

    task = asyncio.create_task(fail_then_cleanup())
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert task.done() is False
    task.cancel()
    await asyncio.sleep(0)
    assert task.done() is False
    release.set()

    with pytest.raises(RuntimeError, match="original failure"):
        await task
    assert finished is True
