from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Coroutine
from pathlib import Path
from typing import Any
from uuid import uuid4

from milky_frog.checkpoint import CheckpointStore
from milky_frog.domain import (
    ModelRequest,
    ModelResponse,
    ReasoningDelta,
    RunCancellation,
    RunRequest,
    RunResult,
    RunState,
    RunStatus,
    SteeringChannel,
    StreamDone,
    TextDelta,
    ToolCall,
    ToolResult,
)
from milky_frog.handlers import (
    AfterModel,
    AfterTool,
    BeforeModel,
    BeforeTool,
    HandlerRegistry,
    OnModelChunk,
    OnModelReasoning,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunPaused,
    RunStarted,
)
from milky_frog.harness.events import (
    model_message_completed,
    run_cancelled,
    run_completed,
    run_failed,
    run_paused,
    run_started,
    tool_call_completed,
    tool_call_requested,
    user_message_added,
)
from milky_frog.harness.sandbox import LocalSandbox
from milky_frog.harness.state import fold, reduce, seal
from milky_frog.harness.tools import ToolContext, ToolRegistry
from milky_frog.models import Model

# Runs whose stop is user-initiated and error-free, so advancing the pending
# work with no new input is safe. COMPLETED has nothing pending; FAILED usually
# recurs on a blind re-advance — both need new input instead (resume with a prompt).
_RESUMABLE = (RunStatus.PAUSED_LIMIT, RunStatus.CANCELLED)

# Any terminal Run can take a new user turn; an active Run (RUNNING / WAITING_*)
# cannot, since only one foreground Run advances at a time. COMPLETED and FAILED
# have nothing pending, so they are continuable only by supplying a prompt.
_CONTINUABLE = (
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.PAUSED_LIMIT,
    RunStatus.CANCELLED,
)


class ResumeError(Exception):
    """A Run cannot be advanced as requested: unknown, has no pending work and
    no prompt was given, or is still active and cannot accept new input."""


class _ToolRunCancelled(Exception):
    """Cooperative cancel arrived while a Tool was executing."""


class Harness:
    """Advances one durable Run through a linear model and Tool loop."""

    def __init__(
        self,
        model: Model,
        tools: ToolRegistry,
        checkpoints: CheckpointStore,
        handlers: HandlerRegistry,
    ) -> None:
        self._model = model
        self._tools = tools
        self._checkpoints = checkpoints
        self._handlers = handlers

    async def run(self, run_request: RunRequest) -> RunResult:
        """Start a fresh Run: seed the transcript from the prompt, then advance."""
        run_id = uuid4().hex
        workspace = run_request.workspace.resolve(strict=True)
        self._checkpoints.create_run(run_id, workspace)
        started = run_started(prompt=run_request.prompt, workspace=workspace)
        self._checkpoints.append(run_id, started)
        await self._handlers.notify(RunStarted(run_id=run_id, request=run_request))
        state = reduce(RunState(run_id=run_id, workspace=workspace), started)
        return await self._advance(
            state,
            LocalSandbox(workspace),
            run_request.cancellation,
            run_request.max_model_calls,
            run_request.steering,
        )

    async def resume(
        self,
        run_id: str,
        *,
        max_model_calls: int,
        cancellation: RunCancellation | None = None,
        prompt: str | None = None,
        steering: SteeringChannel | None = None,
    ) -> RunResult:
        """Advance an existing Run: fold its log into a RunState and repair any
        interrupted Tool, then either pick up its pending work (no prompt) or
        append a new user turn (with prompt) and advance.

        Without a prompt, only a Run stopped with pending work (PAUSED_LIMIT,
        CANCELLED) can be advanced. With a prompt, any terminal Run can — a
        finished conversation is continued by adding the next user message.
        """
        stored = self._checkpoints.get_run(run_id)
        if stored is None:
            raise ResumeError(f"unknown Run: {run_id}")
        if prompt is None and stored.status not in _RESUMABLE:
            raise ResumeError(
                f"Run {run_id} is {stored.status.value} with no pending work; "
                "provide a prompt to continue it"
            )
        if prompt is not None and stored.status not in _CONTINUABLE:
            raise ResumeError(f"Run {run_id} is {stored.status.value} and cannot accept new input")
        state = fold(run_id, stored.workspace, self._checkpoints.events(run_id))
        state, repairs = seal(state)
        for event in repairs:
            self._checkpoints.append(run_id, event)
        if prompt is not None:
            state = self._append_user_message(state, prompt)
        return await self._advance(
            state, LocalSandbox(stored.workspace), cancellation, max_model_calls, steering
        )

    async def _advance(
        self,
        state: RunState,
        sandbox: LocalSandbox,
        cancellation: RunCancellation | None,
        max_model_calls: int,
        steering: SteeringChannel | None = None,
    ) -> RunResult:
        """Drive the model and Tool loop for at most ``max_model_calls`` fresh
        model calls, threading ``RunState`` and growing it only through
        ``reduce``. Steering lines drained between turns are folded in as user
        turns, and a non-empty drain keeps the loop going instead of completing.
        """
        run_id = state.run_id
        try:
            for _ in range(max_model_calls):
                if _is_cancelled(cancellation):
                    return await self._finish_cancelled(state)
                state = self._absorb_steering(state, steering)
                request = ModelRequest(state.messages, self._tools.schemas())
                before_model = BeforeModel(run_id=run_id, request=request)
                await self._handlers.notify(before_model)
                response = await self._consume_stream(run_id, cancellation, before_model.request)
                await self._handlers.notify(
                    AfterModel(run_id=run_id, request=before_model.request, response=response)
                )
                completed = model_message_completed(response)
                self._checkpoints.append(run_id, completed)
                state = reduce(state, completed)

                if not response.tool_calls:
                    # A steering line typed during the final turn turns "done"
                    # into "keep going": fold it in and continue instead of
                    # completing. Otherwise the Run is genuinely finished.
                    steered = self._absorb_steering(state, steering)
                    if steered is not state:
                        state = steered
                        continue
                    return await self._finish_completed(state, response.content)

                for call in response.tool_calls:
                    if _is_cancelled(cancellation):
                        return await self._finish_cancelled(state)
                    try:
                        result = await self._execute_tool(
                            run_id, state.workspace, sandbox, call, cancellation
                        )
                    except _ToolRunCancelled:
                        return await self._finish_cancelled(state)
                    tool_event = tool_call_completed(call, result)
                    self._checkpoints.append(run_id, tool_event)
                    state = reduce(state, tool_event)

            return await self._finish_paused(state, max_model_calls)
        except asyncio.CancelledError:
            if _is_cancelled(cancellation):
                return await self._finish_cancelled(state)
            raise
        except Exception as error:
            self._checkpoints.append(
                run_id,
                run_failed(error),
                RunStatus.FAILED,
            )
            await self._handlers.notify(RunFailed(run_id=run_id, error=error))
            raise

    async def _consume_stream(
        self,
        run_id: str,
        cancellation: RunCancellation | None,
        request: ModelRequest,
    ) -> ModelResponse:
        """Drain a model stream, forwarding text deltas and returning the response.

        Text fragments are dispatched as ``OnModelChunk`` so the UI can render
        live; the terminal ``StreamDone`` carries the assembled response the
        loop needs to decide on tool calls and persist a Checkpoint.
        """
        response: ModelResponse | None = None
        async for chunk in self._model.stream(request):
            if _is_cancelled(cancellation):
                raise asyncio.CancelledError
            if isinstance(chunk, TextDelta):
                await self._handlers.notify(
                    OnModelChunk(run_id=run_id, request=request, chunk=chunk)
                )
            elif isinstance(chunk, ReasoningDelta):
                await self._handlers.notify(
                    OnModelReasoning(run_id=run_id, request=request, chunk=chunk)
                )
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
        sandbox: LocalSandbox,
        call: ToolCall,
        cancellation: RunCancellation | None,
    ) -> ToolResult:
        """Run one Tool call and return its (post-``AfterTool``) result. The
        caller persists and folds the resulting ``ToolCallCompleted`` event, so
        the event has a single construction point shared by store and reduce."""
        await self._handlers.notify(BeforeTool(run_id=run_id, call=call))
        self._checkpoints.append(run_id, tool_call_requested(call))
        tool = self._tools.get(call.name)
        input_model = tool.input_model.model_validate(call.arguments)
        context = ToolContext(run_id, workspace, cancellation, sandbox)
        try:
            result = await self._run_tool_with_cancellation(
                tool.execute(context, input_model), cancellation
            )
        except _ToolRunCancelled:
            raise
        except Exception as error:
            result = ToolResult(f"{type(error).__name__}: {error}", is_error=True)
        after_tool = AfterTool(run_id=run_id, call=call, result=result)
        await self._handlers.notify(after_tool)
        return after_tool.result

    async def _run_tool_with_cancellation(
        self,
        coro: Coroutine[Any, Any, ToolResult],
        cancellation: RunCancellation | None,
    ) -> ToolResult:
        task: asyncio.Task[ToolResult] = asyncio.create_task(coro)
        while not task.done():
            if _is_cancelled(cancellation):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                raise _ToolRunCancelled
            await asyncio.sleep(0)
        return await task

    def _append_user_message(self, state: RunState, content: str) -> RunState:
        """Append a user turn as a durable event and fold it into the state."""
        event = user_message_added(content)
        self._checkpoints.append(state.run_id, event)
        return reduce(state, event)

    def _absorb_steering(self, state: RunState, steering: SteeringChannel | None) -> RunState:
        """Fold any queued steering lines in as user turns.

        Returns the same ``state`` object when nothing was drained, so callers
        can detect whether steering added anything by identity.
        """
        if steering is None:
            return state
        for line in steering.drain():
            state = self._append_user_message(state, line)
        return state

    async def _finish_completed(self, state: RunState, final_message: str) -> RunResult:
        result = RunResult(
            state.run_id,
            RunStatus.COMPLETED,
            final_message,
            state.completed_model_calls,
            state.usage,
        )
        self._checkpoints.append(
            state.run_id,
            run_completed(final_message=final_message),
            RunStatus.COMPLETED,
        )
        await self._handlers.notify(RunCompleted(run_id=state.run_id, result=result))
        return result

    async def _finish_paused(self, state: RunState, max_model_calls: int) -> RunResult:
        message = f"model call limit reached ({max_model_calls})"
        self._checkpoints.append(
            state.run_id,
            run_paused(reason=message, model_calls=state.completed_model_calls),
            RunStatus.PAUSED_LIMIT,
        )
        await self._handlers.notify(
            RunPaused(
                run_id=state.run_id,
                status=RunStatus.PAUSED_LIMIT,
                reason=message,
                model_calls=state.completed_model_calls,
            )
        )
        return RunResult(
            state.run_id,
            RunStatus.PAUSED_LIMIT,
            message,
            state.completed_model_calls,
            state.usage,
        )

    async def _finish_cancelled(self, state: RunState, reason: str = "cancelled") -> RunResult:
        self._checkpoints.append(
            state.run_id,
            run_cancelled(reason=reason, model_calls=state.completed_model_calls),
            RunStatus.CANCELLED,
        )
        await self._handlers.notify(
            RunCancelled(
                run_id=state.run_id, reason=reason, model_calls=state.completed_model_calls
            )
        )
        return RunResult(
            state.run_id,
            RunStatus.CANCELLED,
            reason,
            state.completed_model_calls,
            state.usage,
        )


def _is_cancelled(cancellation: RunCancellation | None) -> bool:
    return cancellation is not None and cancellation.is_cancelled
