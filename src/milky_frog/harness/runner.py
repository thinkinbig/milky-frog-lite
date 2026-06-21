from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Coroutine
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from milky_frog.checkpoint import CheckpointStore, RunClaimError
from milky_frog.domain import (
    ModelRequest,
    ModelResponse,
    ReasoningDelta,
    RunCancellation,
    RunRequest,
    RunResult,
    RunState,
    StreamDone,
    TextDelta,
    ToolCall,
    ToolResult,
)
from milky_frog.handlers import HandlerRegistry
from milky_frog.harness.cancellation import ToolRunCancelled, is_cancelled
from milky_frog.harness.emitter import RunEmitter
from milky_frog.harness.resume import (
    ResumeError,
    ResumeGate,
    completed_tail,
)
from milky_frog.harness.sandbox import LocalSandbox, Sandbox
from milky_frog.harness.state import (
    append_model_response,
    append_tool_result,
    start_run,
)
from milky_frog.harness.tools import ToolContext, ToolRegistry
from milky_frog.models import Model


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
        self._emitter = RunEmitter(checkpoints, handlers)
        self._resume_gate = ResumeGate(checkpoints)

    async def run(self, run_request: RunRequest) -> RunResult:
        """Start a fresh Run: seed the transcript from the prompt, then advance."""
        run_id = uuid4().hex
        workspace = run_request.workspace.resolve(strict=True)
        with self._checkpoints.claim(run_id):
            self._checkpoints.create_run(run_id, workspace)
            state = start_run(RunState(run_id=run_id, workspace=workspace), run_request.prompt)
            await self._emitter.run_started(run_id, run_request, state)
            return await self._advance(
                state,
                LocalSandbox(workspace),
                run_request.cancellation,
                run_request.max_model_calls,
            )

    async def resume(
        self,
        run_id: str,
        *,
        max_model_calls: int,
        cancellation: RunCancellation | None = None,
        prompt: str | None = None,
    ) -> RunResult:
        """Advance an existing Run: load its snapshot and repair any interrupted
        Tool, then either pick up its pending work (no prompt) or append a new user
        turn (with prompt) and advance.

        Without a prompt, only a Run with pending work (PAUSED_LIMIT, CANCELLED,
        or an orphaned RUNNING / WAITING_*) can be advanced. With a prompt, any
        terminal Run can — a finished conversation is continued by adding the
        next user message.
        """
        try:
            with self._checkpoints.claim(run_id):
                stored = self._checkpoints.get_run(run_id)
                stored = ResumeGate.validate(stored, run_id, prompt)
                sandbox = LocalSandbox(stored.workspace)
                plan = self._resume_gate.prepare(
                    run_id,
                    stored,
                    sandbox=sandbox,
                    prompt=prompt,
                    updated_at=stored.updated_at,
                )
                if prompt is None:
                    tail = completed_tail(plan.state)
                    if tail is not None:
                        return await self._emitter.finish_completed(plan.state, tail)
                return await self._advance(plan.state, plan.sandbox, cancellation, max_model_calls)
        except RunClaimError as error:
            raise ResumeError(str(error)) from error

    async def _advance(
        self,
        state: RunState,
        sandbox: Sandbox,
        cancellation: RunCancellation | None,
        max_model_calls: int,
    ) -> RunResult:
        """Drive the model and Tool loop for at most ``max_model_calls`` fresh
        model calls, threading ``RunState`` and growing it through state mutators.
        """
        run_id = state.run_id
        try:
            calls = 0
            while calls < max_model_calls:
                if is_cancelled(cancellation):
                    return await self._emitter.finish_cancelled(state)
                request = ModelRequest(state.messages, self._tools.schemas())
                await self._emitter.before_model(run_id, request)
                response = await self._model_turn(run_id, cancellation, request)
                await self._emitter.after_model(run_id, request, response)
                state = append_model_response(state, response)
                self._emitter.persist(state)
                calls += 1

                if not response.tool_calls:
                    return await self._emitter.finish_completed(state, response.content)

                for call in response.tool_calls:
                    if is_cancelled(cancellation):
                        return await self._emitter.finish_cancelled(state)
                    try:
                        result = await self._execute_tool(
                            run_id, state.workspace, sandbox, call, cancellation
                        )
                    except ToolRunCancelled:
                        return await self._emitter.finish_cancelled(state)
                    state = append_tool_result(state, call, result)
                    self._emitter.persist(state)

            return await self._emitter.finish_paused(state, max_model_calls)
        except asyncio.CancelledError:
            if is_cancelled(cancellation):
                return await self._emitter.finish_cancelled(state)
            raise
        except Exception as error:
            await self._emitter.run_failed(state, error)
            raise

    # ── private helpers (absorbed from ModelStreamer / ToolRunner) ──────────

    async def _model_turn(
        self,
        run_id: str,
        cancellation: RunCancellation | None,
        request: ModelRequest,
    ) -> ModelResponse:
        """Stream the model's response, forwarding text and reasoning deltas live."""
        response: ModelResponse | None = None
        observer_request = deepcopy(request)
        async for chunk in self._model.stream(request):
            if is_cancelled(cancellation):
                raise asyncio.CancelledError
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
        """Run one Tool call with lifecycle signals and cancellation polling."""
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

    @staticmethod
    async def _run_with_cancellation(
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
