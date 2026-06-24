from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Coroutine
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from milky_frog.domain import (
    ModelRequest,
    ModelResponse,
    ReasoningDelta,
    RunCancellation,
    RunResult,
    RunState,
    StreamDone,
    TextDelta,
    ToolCall,
    ToolResult,
    ToolRunCancelled,
    is_cancelled,
)
from milky_frog.harness.emitter import RunEmitter
from milky_frog.harness.model_retry import (
    MODEL_RETRY_BASE_DELAY_S,
    MODEL_RETRY_MAX_ATTEMPTS,
    is_retriable_model_error,
    retry_sleep,
)
from milky_frog.harness.sandbox import Sandbox
from milky_frog.harness.state import append_model_response, append_tool_result
from milky_frog.harness.tools import ToolContext, ToolRegistry
from milky_frog.models import Model


class AgentLoop:
    """Pure async model → tool → model loop.

    Drives the loop, emits lifecycle+streaming events directly to the shared
    ``EventDispatcher`` bus (via ``RunEmitter``).  Knows nothing about
    checkpoints or project config — those are handled by bus subscribers
    (``CheckpointHandler``, ``PolicyHandler``).

    ``advance()`` takes a ``RunState`` and returns a ``RunResult`` — the
    caller (``Harness``) is responsible for seeding / repairing state and
    handling pre-loop approval resolution.
    """

    def __init__(self, model: Model, tools: ToolRegistry, emitter: RunEmitter) -> None:
        self._model = model
        self._tools = tools
        self._emitter = emitter

    async def advance(
        self,
        state: RunState,
        sandbox: Sandbox,
        *,
        max_calls: int = 30,
        cancellation: RunCancellation | None = None,
    ) -> RunResult:
        """Drive at most ``max_calls`` model-tool turns.

        ``state`` is grown in-place (replaced via frozen-dataclass ``replace``)
        and the emitter is notified after every meaningful step so bus
        subscribers (checkpointing, policy, UI, observability) can react.
        """
        run_id = state.run_id
        try:
            while state.completed_model_calls < max_calls:
                if is_cancelled(cancellation):
                    return await self._emitter.finish_cancelled(state)

                request = ModelRequest(state.messages, self._tools.schemas())
                await self._emitter.turn_started(run_id, model_call=state.completed_model_calls + 1)
                before_model_results = await self._emitter.before_model(run_id, request)

                from milky_frog.handlers import BudgetedRequest

                budgeted_requests = [
                    r for r in before_model_results if isinstance(r, BudgetedRequest)
                ]
                if budgeted_requests:
                    request = budgeted_requests[0].request

                try:
                    response = await self._model_turn_with_retry(run_id, request, cancellation)
                except ToolRunCancelled:
                    return await self._emitter.finish_cancelled(state)

                state = append_model_response(state, response)
                await self._emitter.after_model(run_id, request, response, state)

                # No tool calls → agent is done.
                if not response.tool_calls:
                    await self._emitter.turn_ended(run_id, model_call=state.completed_model_calls)
                    return await self._emitter.finish_completed(state, response.content)

                # Execute each tool call with policy check via the bus.
                for call in response.tool_calls:
                    if is_cancelled(cancellation):
                        return await self._emitter.finish_cancelled(state)

                    check_results = await self._emitter.before_tool(run_id, call)

                    from milky_frog.handlers import ApprovalResult, BlockResult

                    blocked = [r for r in check_results if isinstance(r, BlockResult)]
                    approvals = [r for r in check_results if isinstance(r, ApprovalResult)]

                    if blocked:
                        result = ToolResult(blocked[0].reason, is_error=True)
                    elif approvals:
                        return await self._emitter.finish_approval_needed(state, call)
                    else:
                        try:
                            result = await self._execute_tool(
                                run_id,
                                state.workspace,
                                sandbox,
                                call,
                                cancellation,
                            )
                        except ToolRunCancelled:
                            return await self._emitter.finish_cancelled(state)

                    state = append_tool_result(state, call, result)
                    await self._emitter.after_tool(run_id, call, result, state)

                await self._emitter.turn_ended(run_id, model_call=state.completed_model_calls)

            return await self._emitter.finish_paused(state, max_calls)

        except asyncio.CancelledError:
            if is_cancelled(cancellation):
                return await self._emitter.finish_cancelled(state)
            raise
        except Exception as error:
            return await self._emitter.finish_failed(state, error)

    # ── Model + Tool execution helpers ──────────────────────────────

    async def _model_turn_with_retry(
        self,
        run_id: str,
        request: ModelRequest,
        cancellation: RunCancellation | None,
    ) -> ModelResponse:
        for attempt in range(1, MODEL_RETRY_MAX_ATTEMPTS + 1):
            try:
                return cast(
                    ModelResponse,
                    await self._run_cancellable(
                        self._model_turn(run_id, request),
                        cancellation,
                    ),
                )
            except ToolRunCancelled:
                raise
            except Exception as error:
                if not is_retriable_model_error(error) or attempt >= MODEL_RETRY_MAX_ATTEMPTS:
                    raise
                await self._emitter.run_notice(
                    run_id,
                    (
                        f"Cannot reach model ({type(error).__name__}) — "
                        f"retrying ({attempt + 1}/{MODEL_RETRY_MAX_ATTEMPTS})"
                    ),
                    level="warning",
                )
                await self._wait_before_retry(
                    MODEL_RETRY_BASE_DELAY_S * attempt,
                    cancellation,
                )
        raise RuntimeError("model retry loop exited without a response")

    async def _wait_before_retry(
        self, delay_s: float, cancellation: RunCancellation | None
    ) -> None:
        if is_cancelled(cancellation):
            raise ToolRunCancelled
        await retry_sleep(delay_s)
        if is_cancelled(cancellation):
            raise ToolRunCancelled

    async def _model_turn(
        self,
        run_id: str,
        request: ModelRequest,
    ) -> ModelResponse:
        """Stream one model response, emitting chunk events to the bus."""
        response: ModelResponse | None = None
        observer_request = deepcopy(request)
        async with contextlib.aclosing(self._model.stream(request)) as model_stream:
            async for chunk in model_stream:
                if isinstance(chunk, TextDelta):
                    await self._emitter.on_model_chunk(run_id, observer_request, chunk)
                elif isinstance(chunk, ReasoningDelta):
                    await self._emitter.on_model_reasoning(run_id, observer_request, chunk)
                elif isinstance(chunk, StreamDone):
                    response = chunk.response
                    break
        if response is None:
            raise RuntimeError("model stream ended without a StreamDone chunk")
        return response

    async def _execute_tool(
        self,
        run_id: str,
        workspace: Path,
        sandbox: Sandbox,
        call: ToolCall,
        cancellation: RunCancellation | None,
    ) -> ToolResult:
        tool = self._tools.get(call.name)
        context = ToolContext(run_id, workspace, cancellation, sandbox)
        try:
            input_model = tool.input_model.model_validate(call.arguments)
            result: ToolResult = await self._run_cancellable(
                tool.execute(context, input_model), cancellation
            )
        except ToolRunCancelled:
            raise
        except Exception as error:
            result = ToolResult(f"{type(error).__name__}: {error}", is_error=True)
        return result

    @staticmethod
    async def _wait_for_cancellation(cancellation: RunCancellation) -> None:
        while not cancellation.is_cancelled:
            await asyncio.sleep(0.05)

    @staticmethod
    async def _run_cancellable(
        coro: Coroutine[Any, Any, Any],
        cancellation: RunCancellation | None,
    ) -> Any:
        task: asyncio.Task[Any] = asyncio.create_task(coro)
        poll: asyncio.Task[None] | None = None
        if cancellation is not None:
            poll = asyncio.create_task(AgentLoop._wait_for_cancellation(cancellation))
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
                with contextlib.suppress(asyncio.CancelledError):
                    await poll
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task


# ── Standalone tool executor (shared by Harness approval resolution) ──


async def execute_tool(
    tools: ToolRegistry,
    run_id: str,
    workspace: Path,
    sandbox: Sandbox,
    call: ToolCall,
    cancellation: RunCancellation | None,
) -> ToolResult:
    """Execute one tool call with cancellation polling.

    Shared between ``AgentLoop`` (inline) and ``Harness`` (approval-
    resolution tool execution before the loop starts).
    """
    tool = tools.get(call.name)
    context = ToolContext(run_id, workspace, cancellation, sandbox)
    try:
        input_model = tool.input_model.model_validate(call.arguments)
        result: ToolResult = await AgentLoop._run_cancellable(
            tool.execute(context, input_model), cancellation
        )
    except ToolRunCancelled:
        raise
    except Exception as error:
        result = ToolResult(f"{type(error).__name__}: {error}", is_error=True)
    return result
