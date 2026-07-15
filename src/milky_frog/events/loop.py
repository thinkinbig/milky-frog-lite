from __future__ import annotations

import asyncio
import contextlib
from copy import deepcopy
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from milky_frog.core.runtime.execute_tool import run_cancellable
from milky_frog.core.sandbox import Sandbox
from milky_frog.domain import (
    DEFAULT_MAX_MODEL_CALLS,
    Compacted,
    HandlerResult,
    ModelRequest,
    ModelResponse,
    ReasoningDelta,
    RunCancellation,
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
from milky_frog.events.hub import EventHub
from milky_frog.events.tool_step import ToolStepExecutor
from milky_frog.harness.context import ContextManager
from milky_frog.harness.state import (
    append_model_response,
    append_synthetic_tool_call,
    append_tool_result,
)
from milky_frog.harness.tools import ToolRegistry
from milky_frog.models import Model

if TYPE_CHECKING:
    from milky_frog.harness.budget import TokenBudget


def _apply_control(
    state: RunState, results: list[HandlerResult]
) -> tuple[RunState, Compacted | None]:
    """Apply Handler control proposals from ``before_model`` to the ``RunState``.

    The loop owns RunState evolution; Handlers only propose. Handles
    ``Compacted`` (from ``before_model``).

    Returns: (updated state, the applied ``Compacted`` or ``None``) — the loop
    emits ``run_compaction`` from the proposal so the UI can render and bill it.
    """
    applied: Compacted | None = None
    for result in results:
        match result:
            case Compacted() as compacted:
                state = replace(state, compaction=compacted.compaction)
                applied = compacted
    return state, applied


class AgentLoop:
    """Pure async model → tool → model loop.

    Drives the loop and publishes lifecycle+streaming events on the shared
    ``EventHub``.  Knows nothing about checkpoints or project config —
    those are handled by bus subscribers (``CheckpointHandler``).

    ``advance()`` takes a ``RunState`` and returns a ``RunResult`` — the
    caller (``Harness``) is responsible for seeding / repairing state and
    handling pre-loop approval resolution.
    """

    def __init__(
        self,
        model: Model,
        tools: ToolRegistry,
        hub: EventHub,
        tool_step: ToolStepExecutor,
        context: ContextManager,
    ) -> None:
        self._model = model
        self._tools = tools
        self._hub = hub
        self._tool_step = tool_step
        self._context = context

    async def advance(
        self,
        state: RunState,
        sandbox: Sandbox,
        *,
        max_calls: int = DEFAULT_MAX_MODEL_CALLS,
        cancellation: RunCancellation | None = None,
        budget: TokenBudget | None = None,
    ) -> RunResult:
        """Drive at most ``max_calls`` model-tool turns.

        ``state`` is grown in-place (replaced via frozen-dataclass ``replace``)
        and the hub broadcasts after every meaningful step so Handlers
        subscribers (checkpointing, policy, UI, observability) can react.
        """
        run_id = state.run_id
        try:
            while max_calls <= 0 or state.completed_model_calls < max_calls:
                if is_cancelled(cancellation):
                    return await self._hub.finish_cancelled(state)

                request = ModelRequest(
                    self._context.assemble(state), self._tools.schemas(), run_id=run_id
                )
                model_call = state.completed_model_calls + 1
                await self._hub.turn_started(run_id, model_call=model_call)
                shaping = await self._hub.before_model(run_id, request, state)
                shaped, compacted = _apply_control(state, shaping)
                if shaped is not state:
                    state = shaped
                    if compacted is not None:
                        await self._hub.run_compaction(
                            run_id, compacted.messages_folded, compacted.usage
                        )
                    request = ModelRequest(
                        self._context.assemble(state), self._tools.schemas(), run_id=run_id
                    )
                if budget is not None:
                    request = budget.trim(request)

                try:
                    response = cast(
                        ModelResponse,
                        await run_cancellable(
                            self._model_turn(run_id, request),
                            cancellation,
                        ),
                    )
                except ToolRunCancelled:
                    return await self._hub.finish_cancelled(state)

                state = append_model_response(state, response)
                await self._hub.after_model(run_id, request, response, state)

                # No tool calls → agent is done.
                if not response.tool_calls:
                    model_call = state.completed_model_calls
                    await self._hub.turn_ended(run_id, model_call=model_call)
                    return await self._hub.finish_completed(state, response.content)

                if is_cancelled(cancellation):
                    return await self._hub.finish_cancelled(state)

                # Split the batch once: calls that can run right now (ALLOW or
                # DENY — both resolve without a human) vs. calls that need
                # approval (a whole-Run pause). The runnable subset always runs
                # concurrently first and is fully folded into ``state`` before
                # any halt, so halting on the approval subset never leaves
                # already-started work with nowhere to go.
                decisions = [self._tool_step.decide(call) for call in response.tool_calls]
                runnable = [
                    (call, decision)
                    for call, decision in zip(response.tool_calls, decisions, strict=True)
                    if decision is not ToolDecision.NEEDS_APPROVAL
                ]
                needs_approval = tuple(
                    call
                    for call, decision in zip(response.tool_calls, decisions, strict=True)
                    if decision is ToolDecision.NEEDS_APPROVAL
                )

                if runnable:
                    state, cancelled, resolved = await self._execute_decided_batch(
                        run_id, state, sandbox, runnable, cancellation
                    )

                    # A Tool's outcome can request a follow-up call (e.g. subagent
                    # leaving an unmerged worktree) that must pause the Run for a
                    # human decision — deterministically, not by hoping the model
                    # raises it. The synthesized call goes through the same policy
                    # check as any model-issued call: NEEDS_APPROVAL joins this
                    # batch's own pause, ALLOW/DENY execute immediately.
                    synthesized: list[ToolCall] = []
                    for call, outcome in resolved:
                        if outcome.follow_up is not None:
                            synthetic = ToolCall(
                                id=f"{call.id}:follow-up",
                                name=outcome.follow_up.tool_name,
                                arguments=outcome.follow_up.arguments,
                            )
                            state = append_synthetic_tool_call(state, synthetic)
                            synthesized.append(synthetic)

                    if cancelled:
                        return await self._hub.finish_cancelled(state)

                    if synthesized:
                        synth_decisions = [
                            (call, self._tool_step.decide(call)) for call in synthesized
                        ]
                        synth_runnable = [
                            (call, decision)
                            for call, decision in synth_decisions
                            if decision is not ToolDecision.NEEDS_APPROVAL
                        ]
                        needs_approval = needs_approval + tuple(
                            call
                            for call, decision in synth_decisions
                            if decision is ToolDecision.NEEDS_APPROVAL
                        )
                        state, synth_cancelled, _synth_resolved = await self._execute_decided_batch(
                            run_id, state, sandbox, synth_runnable, cancellation
                        )
                        if synth_cancelled:
                            return await self._hub.finish_cancelled(state)

                if needs_approval:
                    # No ``before_tool`` here: it fires when the approved call
                    # actually executes (``AgentHarness._apply_approvals``), the
                    # one emission point that also covers cross-process resume.
                    # Emitting it at pause time too would double every subscriber
                    # — duplicate TUI tool cards, orphaned Langfuse spans.
                    return await self._hub.finish_approval_needed(state, needs_approval)

                model_call = state.completed_model_calls
                await self._hub.turn_ended(run_id, model_call=model_call)

            # Only reachable when max_calls > 0 (unlimited loops never pause).
            return await self._hub.finish_paused(state, max_calls)

        except asyncio.CancelledError:
            if is_cancelled(cancellation):
                return await self._hub.finish_cancelled(state)
            raise
        except Exception as error:
            return await self._hub.finish_failed(state, error)

    async def _execute_decided_batch(
        self,
        run_id: str,
        state: RunState,
        sandbox: Sandbox,
        decided: list[tuple[ToolCall, ToolDecision]],
        cancellation: RunCancellation | None,
    ) -> tuple[RunState, bool, list[tuple[ToolCall, ToolResult]]]:
        """Execute a batch of calls whose policy decision is already known.

        Shared by the model-issued batch and any harness-synthesized follow-up
        calls (``ToolResult.follow_up``) that resolve to ALLOW/DENY instead of
        NEEDS_APPROVAL — both fold their results into ``state`` the same way.
        Returns the updated state, whether anything was cancelled, and the
        resolved ``(call, result)`` pairs so the caller can inspect them (e.g.
        for a further follow-up).
        """
        if not decided:
            return state, False, []
        for call, _decision in decided:
            await self._hub.before_tool(run_id, call)

        batch = [
            (
                call,
                self._tool_step.execute_decided(
                    run_id, state.workspace, sandbox, call, cancellation, decision
                ),
            )
            for call, decision in decided
        ]
        resolved, cancelled = await self._tool_step.resolve_batch(batch)

        for call, outcome in resolved:
            state = append_tool_result(state, call, outcome)
            await self._hub.after_tool(run_id, call, outcome, state)

        return state, cancelled, resolved

    async def _model_turn(
        self,
        run_id: str,
        request: ModelRequest,
    ) -> ModelResponse:
        response: ModelResponse | None = None
        observer_request = deepcopy(request)
        async with contextlib.aclosing(self._model.stream(request)) as model_stream:
            async for chunk in model_stream:
                if isinstance(chunk, TextDelta):
                    await self._hub.on_model_chunk(run_id, observer_request, chunk)
                elif isinstance(chunk, ReasoningDelta):
                    await self._hub.on_model_reasoning(run_id, observer_request, chunk)
                elif isinstance(chunk, StreamDone):
                    response = chunk.response
                    break
        if response is None:
            raise RuntimeError("model stream ended without a StreamDone chunk")
        return response
