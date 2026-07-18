"""Cancellation-safe helpers for releasing acquired runtime resources."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable


async def complete_cleanup[T](
    cleanup: Awaitable[T],
    *,
    propagate_cancellation: bool,
) -> T:
    """Run ``cleanup`` to completion even if its caller is cancelled.

    ``asyncio.shield`` prevents cancellation from reaching the cleanup Task,
    while the loop also absorbs repeated cancellation requests until that Task
    has finished. Callers handling an earlier failure can keep that original
    exception by setting ``propagate_cancellation=False``; successful paths set
    it to ``True`` so cancellation is re-raised after resources are safe.

    Coroutine awaitables run in a dedicated Task; a Task/Future passed by the
    caller keeps its existing owner. Do not pass a coroutine for an async
    context whose exit must run in the same Task as its entry; such resources
    need an owner Task that performs both operations (as MCP connections do).
    """
    task = asyncio.ensure_future(cleanup)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True

    result = task.result()
    if cancelled and propagate_cancellation:
        raise asyncio.CancelledError
    return result
