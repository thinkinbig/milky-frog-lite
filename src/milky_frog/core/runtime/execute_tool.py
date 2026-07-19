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
    """Execute one tool call with cancellation polling.

    Every failure mode — including an unknown Tool name — is converted to an
    error ``ToolResult`` here; callers (including concurrent batches via
    ``asyncio.gather(..., return_exceptions=True)``) can rely on this
    function never raising anything but ``ToolRunCancelled``.
    """
    try:
        tool = tools.get(call.name)
        search_prefix = _extract_search_prefix(call.arguments)
        context = ToolContext(
            run_id,
            workspace,
            cancellation,
            sandbox=sandbox,
            token_counter=token_counter,
            search_prefix=search_prefix,
        )
        input_model = tool.input_model.model_validate(call.arguments)
        result: ToolResult = await run_cancellable(
            tool.execute(context, input_model), cancellation, keep_completed_work=True
        )
    except ToolRunCancelled:
        raise
    except Exception as error:
        result = ToolResult(f"{type(error).__name__}: {error}", is_error=True)
    return result


def _extract_search_prefix(arguments: dict[str, Any]) -> str:
    """Extract the search path from tool arguments to use as path prefix for output.

    Tools that search in subdirectories (grep, bash, etc.) need to output paths
    that are workspace-relative. This function extracts the 'path' argument
    (which is the search scope) and returns it as a prefix, defaulting to "" if
    the path is "." or missing.
    """
    path = arguments.get("path", ".")
    if isinstance(path, str) and path != ".":
        return path
    return ""


async def wait_for_cancellation(cancellation: RunCancellation) -> None:
    while not cancellation.is_cancelled:
        await asyncio.sleep(0.05)


async def run_cancellable(
    coro: Coroutine[Any, Any, Any],
    cancellation: RunCancellation | None,
    *,
    keep_completed_work: bool = False,
) -> Any:
    """Await ``coro``, raising ``ToolRunCancelled`` once ``cancellation`` fires.

    ``asyncio.wait`` can report the work and the cancellation poll as done
    together, when cancellation lands in the same event-loop iteration the work
    finishes in. ``keep_completed_work`` decides that tie: Tool execution sets
    it, because a Tool that already ran has side effects on disk and dropping
    its result would strand them (resume repairs the missing result into an
    "interrupted" one, so a completed ``write_file`` silently vanishes from the
    transcript). A model turn leaves it False — nothing has happened yet, and
    honoring the interrupt matters more than keeping a response that would only
    carry the Run onward into more tool calls.
    """
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
        if keep_completed_work and task in done:
            return task.result()
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
