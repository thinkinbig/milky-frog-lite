from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

from milky_frog.core.sandbox import Sandbox
from milky_frog.domain import RunCancellation, ToolCall, ToolResult, ToolRunCancelled
from milky_frog.harness.tools import ToolContext, ToolRegistry
from milky_frog.tokens import TokenCounter


async def execute_tool(
    tools: ToolRegistry,
    run_id: str,
    workspace: Path,
    sandbox: Sandbox,
    call: ToolCall,
    cancellation: RunCancellation | None,
    *,
    token_counter: TokenCounter | None = None,
) -> ToolResult:
    """Execute one tool call with cancellation polling."""
    tool = tools.get(call.name)
    context = ToolContext(
        run_id,
        workspace,
        cancellation,
        sandbox=sandbox,
        token_counter=token_counter,
    )
    try:
        input_model = tool.input_model.model_validate(call.arguments)
        result: ToolResult = await run_cancellable(tool.execute(context, input_model), cancellation)
    except ToolRunCancelled:
        raise
    except Exception as error:
        result = ToolResult(f"{type(error).__name__}: {error}", is_error=True)
    return result


async def wait_for_cancellation(cancellation: RunCancellation) -> None:
    while not cancellation.is_cancelled:
        await asyncio.sleep(0.05)


async def run_cancellable(
    coro: Coroutine[Any, Any, Any],
    cancellation: RunCancellation | None,
) -> Any:
    task: asyncio.Task[Any] = asyncio.create_task(coro)
    poll: asyncio.Task[None] | None = None
    if cancellation is not None:
        poll = asyncio.create_task(wait_for_cancellation(cancellation))
    try:
        if poll is None:
            return await task
        done, _pending = await asyncio.wait(
            {task, poll},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if poll in done:
            raise ToolRunCancelled
        return task.result()
    finally:
        if poll is not None and not poll.done():
            poll.cancel()
            with contextlib.suppress(asyncio.CancelledError, RuntimeError):
                await poll
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, RuntimeError):
                await task
