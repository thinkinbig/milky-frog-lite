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
    ToolDecision,
    ToolResult,
    ToolRunCancelled,
    is_cancelled,
)
from milky_frog.gates import PreparedRun, ResumeError, ResumeGate, ToolGate
from milky_frog.handlers import LifecycleBus
from milky_frog.harness.emitter import RunEmitter
from milky_frog.harness.sandbox import LocalSandbox, Sandbox, SandboxFactory
from milky_frog.harness.state import (
    append_model_response,
    append_tool_result,
    start_run,
    unmatched_tool_calls,
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
        handlers: LifecycleBus,
        sandbox_factory: SandboxFactory = LocalSandbox,
        tool_gate: ToolGate | None = None,
    ) -> None:
        self._model = model
        self._tools = tools
        self._checkpoints = checkpoints
        self._sandbox_factory = sandbox_factory
        self._emitter = RunEmitter(checkpoints, handlers)
        self._resume_gate = ResumeGate(checkpoints)
        self._tool_gate = tool_gate

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
                self._sandbox_factory(workspace),
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
        """Advance an existing Run: load its snapshot, repair interrupted
        Tools, optionally append a new user turn, then advance."""
        try:
            with self._checkpoints.claim(run_id):
                stored = self._checkpoints.get_run(run_id)
                stored = ResumeGate.validate(stored, run_id, prompt)
                sandbox = self._sandbox_factory(stored.workspace)
                plan = self._resume_gate.prepare(
                    run_id,
                    stored,
                    sandbox=sandbox,
                    prompt=prompt,
                    updated_at=stored.updated_at,
                )
                # Process pending tool approvals before advancing.
                resolved = await self._apply_approvals(plan, run_id, sandbox, cancellation)
                if isinstance(resolved, RunResult):
                    return resolved
                plan = resolved
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
                await self._emitter.turn_started(run_id, model_call=calls + 1)
                await self._emitter.before_model(run_id, request)
                try:
                    response = await self._run_cancellable(
                        self._model_turn(run_id, request), cancellation
                    )
                except ToolRunCancelled:
                    return await self._emitter.finish_cancelled(state)
                await self._emitter.after_model(run_id, request, response)
                state = append_model_response(state, response)
                self._emitter.persist(state)
                calls += 1

                if not response.tool_calls:
                    await self._emitter.turn_ended(run_id, model_call=calls)
                    return await self._emitter.finish_completed(state, response.content)

                for call in response.tool_calls:
                    if is_cancelled(cancellation):
                        return await self._emitter.finish_cancelled(state)
                    if self._tool_gate is not None:
                        decision = self._tool_gate.check(call)
                    else:
                        decision = ToolDecision.ALLOW
                    if decision is ToolDecision.DENY:
                        result = ToolResult("denied by tool policy", is_error=True)
                    elif decision is ToolDecision.NEEDS_APPROVAL:
                        return await self._emitter.finish_approval_needed(state, [call.name])
                    else:
                        try:
                            result = await self._execute_tool(
                                run_id, state.workspace, sandbox, call, cancellation
                            )
                        except ToolRunCancelled:
                            return await self._emitter.finish_cancelled(state)
                    state = append_tool_result(state, call, result)
                    self._emitter.persist(state)

                await self._emitter.turn_ended(run_id, model_call=calls)

            return await self._emitter.finish_paused(state, max_model_calls)
        except asyncio.CancelledError:
            if is_cancelled(cancellation):
                return await self._emitter.finish_cancelled(state)
            raise
        except Exception as error:
            await self._emitter.run_failed(state, error)
            raise

    # ── private helpers (absorbed from ModelStreamer / ToolRunner) ──────────

    async def _apply_approvals(
        self,
        plan: PreparedRun,
        run_id: str,
        sandbox: Sandbox,
        cancellation: RunCancellation | None,
    ) -> PreparedRun | RunResult:
        """Execute or deny tool calls that were pending approval on resume.

        Returns an updated ``PreparedRun`` when all pending calls are resolved,
        or a ``RunResult`` if approval is still needed (re-pause).
        """
        pending = unmatched_tool_calls(plan.state.messages)
        if not pending or self._tool_gate is None:
            return plan
        for call in pending:
            if is_cancelled(cancellation):
                return await self._emitter.finish_cancelled(plan.state)
            decision = self._tool_gate.check(call)
            if decision is ToolDecision.ALLOW:
                try:
                    result = await self._execute_tool(
                        run_id, plan.state.workspace, sandbox, call, cancellation
                    )
                except ToolRunCancelled:
                    return await self._emitter.finish_cancelled(plan.state)
            elif decision is ToolDecision.DENY:
                result = ToolResult("denied by user", is_error=True)
            else:
                return await self._emitter.finish_approval_needed(plan.state, [call.name])
            plan = PreparedRun(
                state=append_tool_result(plan.state, call, result),
                sandbox=plan.sandbox,
            )
            self._emitter.persist(plan.state)
        return plan

    async def _model_turn(
        self,
        run_id: str,
        request: ModelRequest,
    ) -> ModelResponse:
        """Stream model response — cancellation polling is handled by the
        caller via ``_run_cancellable``."""
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
        """Run one Tool call with lifecycle signals and cancellation polling."""
        await self._emitter.before_tool(run_id, call)
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
        await self._emitter.after_tool(run_id, call, result)
        return result

    @staticmethod
    async def _run_cancellable(
        coro: Coroutine[Any, Any, Any],
        cancellation: RunCancellation | None,
    ) -> Any:
        """Run *coro* as a task and cancel it when *cancellation* is set."""
        task: asyncio.Task[Any] = asyncio.create_task(coro)
        while not task.done():
            if is_cancelled(cancellation):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                raise ToolRunCancelled
            await asyncio.sleep(0)
        return await task
