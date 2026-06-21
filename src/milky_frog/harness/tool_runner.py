from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

from milky_frog.domain import RunCancellation, ToolCall, ToolResult
from milky_frog.harness.cancellation import ToolRunCancelled, is_cancelled
from milky_frog.harness.emitter import RunEmitter
from milky_frog.harness.sandbox import Sandbox
from milky_frog.harness.tools import ToolContext, ToolRegistry


class ToolRunner:
    """Execute one Tool call, emitting lifecycle signals through the RunEmitter."""

    def __init__(self, tools: ToolRegistry, emitter: RunEmitter) -> None:
        self._tools = tools
        self._emitter = emitter

    async def execute(
        self,
        run_id: str,
        workspace: Path,
        sandbox: Sandbox,
        call: ToolCall,
        cancellation: RunCancellation | None,
    ) -> ToolResult:
        """Run one Tool call and return its result.

        The caller persists and folds the resulting tool message into ``RunState``.
        """
        await self._emitter.before_tool(run_id, call)
        tool = self._tools.get(call.name)
        context = ToolContext(run_id, workspace, cancellation, sandbox)
        try:
            input_model = tool.input_model.model_validate(call.arguments)
            result = await self._run_with_cancellation(
                tool.execute(context, input_model), cancellation
            )
        except ToolRunCancelled:
            raise
        except Exception as error:
            result = ToolResult(f"{type(error).__name__}: {error}", is_error=True)
        await self._emitter.after_tool(run_id, call, result)
        return result

    async def _run_with_cancellation(
        self,
        coro: Coroutine[Any, Any, ToolResult],
        cancellation: RunCancellation | None,
    ) -> ToolResult:
        task: asyncio.Task[ToolResult] = asyncio.create_task(coro)
        while not task.done():
            if is_cancelled(cancellation):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                raise ToolRunCancelled
            await asyncio.sleep(0)
        return await task
