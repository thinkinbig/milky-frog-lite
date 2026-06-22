from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Coroutine
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from milky_frog.checkpoint import CheckpointStore, RunClaimError
from milky_frog.domain import (
    ApprovalDecision,
    ModelRequest,
    ModelResponse,
    ReasoningDelta,
    ResumeError,
    RunCancellation,
    RunRequest,
    RunResult,
    RunState,
    RunStatus,
    StreamDone,
    TextDelta,
    ToolCall,
    ToolResult,
    ToolRunCancelled,
    is_cancelled,
)
from milky_frog.handlers import ApprovalResult, BlockResult, EventDispatcher
from milky_frog.harness.emitter import RunEmitter
from milky_frog.harness.model_retry import (
    MODEL_RETRY_BASE_DELAY_S,
    MODEL_RETRY_MAX_ATTEMPTS,
    is_retriable_model_error,
    retry_sleep,
)
from milky_frog.harness.sandbox import LocalSandbox, Sandbox, SandboxFactory
from milky_frog.harness.state import (
    append_model_response,
    append_tool_result,
    append_user_message,
    seal,
    start_run,
    unmatched_tool_calls,
)
from milky_frog.harness.tools import ToolContext, ToolRegistry
from milky_frog.models import Model


@dataclass(frozen=True, slots=True)
class PreparedRun:
    """State and sandbox prepared for ``Harness._advance`` after a resume."""

    state: RunState
    sandbox: Sandbox


class Harness:
    """Advances one durable Run through a linear model and Tool loop.

    The Harness uses ``checkpoints`` directly for claiming and seeding Runs, but
    persistence-on-lifecycle is wired through the bus: the ``handlers`` bus must
    already carry a ``CheckpointHandler`` (see ``handlers.default_handlers``) for
    a Run to be resumable.
    """

    def __init__(
        self,
        model: Model,
        tools: ToolRegistry,
        checkpoints: CheckpointStore,
        handlers: EventDispatcher,
        sandbox_factory: SandboxFactory = LocalSandbox,
        agent_home: Path | None = None,
    ) -> None:
        self._model = model
        self._tools = tools
        self._checkpoints = checkpoints
        self._sandbox_factory = sandbox_factory
        self._handlers = handlers
        self._emitter = RunEmitter(handlers)
        self._agent_home = agent_home

    async def run(self, run_request: RunRequest) -> RunResult:
        """Start a fresh Run: seed the transcript from the prompt, then advance."""
        run_id = uuid4().hex
        workspace = run_request.workspace.resolve(strict=True)
        with self._checkpoints.claim(run_id):
            self._checkpoints.create_run(run_id, workspace)
            extra_sections = await self._emitter.run_before_start(run_id, run_request, workspace)
            state = start_run(
                RunState(run_id=run_id, workspace=workspace),
                run_request.prompt,
                extra_sections,
                agent_home=self._agent_home,
            )
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
        approval: ApprovalDecision | None = None,
    ) -> RunResult:
        """Advance an existing Run: load its snapshot, repair interrupted
        Tools, optionally append a new user turn, then advance.

        ``approval`` releases a Run paused on ``WAITING_FOR_APPROVAL``:
        ``APPROVE`` executes the pending tool calls, ``DENY`` seals them
        with a refusal result.  When ``None``, pending calls are re-checked
        against the tool policy (which may pause again)."""
        try:
            with self._checkpoints.claim(run_id):
                stored = self._checkpoints.get_run(run_id)
                if stored is None:
                    raise ResumeError(f"unknown Run: {run_id}")

                sandbox = self._sandbox_factory(stored.workspace)

                # Notify observers before preparing state.
                await self._emitter.before_resume(run_id, prompt, stored.status)

                # Load snapshot, seal interrupted tools, optionally append prompt.
                state = self._checkpoints.load_state(run_id)
                if stored.status is not RunStatus.WAITING_FOR_APPROVAL:
                    state, _ = seal(state)
                if prompt is not None:
                    state = append_user_message(state, prompt)
                self._checkpoints.prepare_resume(run_id, stored.updated_at, state)

                plan = PreparedRun(state=state, sandbox=sandbox)

                # Process pending tool approvals before advancing.
                resolved = await self._apply_approvals(
                    plan, run_id, sandbox, cancellation, approval
                )
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
                    response = await self._model_turn_with_retry(run_id, request, cancellation)
                except ToolRunCancelled:
                    return await self._emitter.finish_cancelled(state)
                state = append_model_response(state, response)
                await self._emitter.after_model(run_id, request, response, state)
                calls += 1

                if not response.tool_calls:
                    await self._emitter.turn_ended(run_id, model_call=calls)
                    return await self._emitter.finish_completed(state, response.content)

                for call in response.tool_calls:
                    if is_cancelled(cancellation):
                        return await self._emitter.finish_cancelled(state)

                    # Unified control + observation: RunBeforeTool.
                    check_results = await self._emitter.before_tool(run_id, call)
                    blocked = [r for r in check_results if isinstance(r, BlockResult)]
                    approvals = [r for r in check_results if isinstance(r, ApprovalResult)]

                    if blocked:
                        result = ToolResult(blocked[0].reason, is_error=True)
                    elif approvals:
                        return await self._emitter.finish_approval_needed(state, [call.name])
                    else:
                        try:
                            result = await self._execute_tool(
                                run_id, state.workspace, sandbox, call, cancellation
                            )
                        except ToolRunCancelled:
                            return await self._emitter.finish_cancelled(state)
                    state = append_tool_result(state, call, result)
                    await self._emitter.after_tool(run_id, call, result, state)

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
        approval: ApprovalDecision | None,
    ) -> PreparedRun | RunResult:
        """Resolve tool calls that were pending approval on resume.

        ``approval`` is the user's verdict: ``DENY`` seals each pending call
        with a refusal, ``APPROVE`` executes it, and ``None`` re-checks it
        against the tool policy (which may re-pause).  Returns an updated
        ``PreparedRun`` when all pending calls are resolved, or a ``RunResult``
        when the Run terminates (re-pause, denial-as-cancel, or cancellation).
        """
        pending = unmatched_tool_calls(plan.state.messages)
        if not pending:
            return plan
        for call in pending:
            if is_cancelled(cancellation):
                return await self._emitter.finish_cancelled(plan.state)

            resolved = await self._resolve_pending_call(
                plan, run_id, sandbox, call, cancellation, approval
            )
            if isinstance(resolved, RunResult):
                return resolved
            plan = PreparedRun(
                state=append_tool_result(plan.state, call, resolved),
                sandbox=plan.sandbox,
            )
            await self._emitter.after_tool(run_id, call, resolved, plan.state)
        return plan

    async def _resolve_pending_call(
        self,
        plan: PreparedRun,
        run_id: str,
        sandbox: Sandbox,
        call: ToolCall,
        cancellation: RunCancellation | None,
        approval: ApprovalDecision | None,
    ) -> ToolResult | RunResult:
        """Decide one pending call's fate; ``RunResult`` ends the Run."""
        if approval is ApprovalDecision.DENY:
            return ToolResult("denied by user", is_error=True)
        if approval is ApprovalDecision.APPROVE:
            try:
                return await self._execute_tool(
                    run_id, plan.state.workspace, sandbox, call, cancellation
                )
            except ToolRunCancelled:
                return await self._emitter.finish_cancelled(plan.state)

        # No verdict: fall back to the tool policy, which may pause again.
        check_results = await self._emitter.before_tool(run_id, call)
        blocked = [r for r in check_results if isinstance(r, BlockResult)]
        approvals = [r for r in check_results if isinstance(r, ApprovalResult)]
        if blocked:
            return ToolResult(blocked[0].reason, is_error=True)
        if approvals:
            return await self._emitter.finish_approval_needed(plan.state, [call.name])
        try:
            return await self._execute_tool(
                run_id, plan.state.workspace, sandbox, call, cancellation
            )
        except ToolRunCancelled:
            return await self._emitter.finish_cancelled(plan.state)

    async def _model_turn_with_retry(
        self,
        run_id: str,
        request: ModelRequest,
        cancellation: RunCancellation | None,
    ) -> ModelResponse:
        """Call the model, retrying transient connection failures with a Run notice."""
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
                await self._wait_before_model_retry(
                    MODEL_RETRY_BASE_DELAY_S * attempt,
                    cancellation,
                )
        raise RuntimeError("model retry loop exited without a response")

    async def _wait_before_model_retry(
        self,
        delay_s: float,
        cancellation: RunCancellation | None,
    ) -> None:
        """Pause between model retries, honouring cooperative cancellation."""
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
        """Run one Tool call with cancellation polling."""
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
    async def _run_cancellable(
        coro: Coroutine[Any, Any, Any],
        cancellation: RunCancellation | None,
    ) -> Any:
        """Run *coro* as a task, cancelling it on cooperative or external cancel.

        The ``finally`` clause guarantees the child task is cancelled on *any*
        exit — cooperative (``ToolRunCancelled``) or external (``CancelledError``
        injected by the host, e.g. a TUI worker abort). Without it the child
        keeps running as an orphan and continues streaming after the Run was
        reported cancelled.
        """
        task: asyncio.Task[Any] = asyncio.create_task(coro)
        try:
            while not task.done():
                if is_cancelled(cancellation):
                    raise ToolRunCancelled
                await asyncio.sleep(0)
            return await task
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
