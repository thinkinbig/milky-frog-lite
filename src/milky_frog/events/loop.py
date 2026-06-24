from __future__ import annotations

import asyncio
import contextlib
from copy import deepcopy
from typing import TYPE_CHECKING, cast

from milky_frog.core.runtime.execute_tool import run_cancellable
from milky_frog.core.sandbox import Sandbox
from milky_frog.domain import (
    ModelRequest,
    ModelResponse,
    ReasoningDelta,
    RunCancellation,
    RunResult,
    RunState,
    StreamDone,
    TextDelta,
    ToolRunCancelled,
    is_cancelled,
)
from milky_frog.events.hub import EventHub
from milky_frog.events.tool_step import ToolStepExecutor
from milky_frog.harness.state import append_model_response, append_tool_result
from milky_frog.harness.tools import ToolRegistry
from milky_frog.models import Model

if TYPE_CHECKING:
    from milky_frog.harness.tokens import TokenBudget


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
        self, model: Model, tools: ToolRegistry, hub: EventHub, tool_step: ToolStepExecutor
    ) -> None:
        self._model = model
        self._tools = tools
        self._hub = hub
        self._tool_step = tool_step

    async def advance(
        self,
        state: RunState,
        sandbox: Sandbox,
        *,
        max_calls: int = 30,
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

                request = ModelRequest(state.messages, self._tools.schemas(), run_id=run_id)
                model_call = state.completed_model_calls + 1
                await self._hub.turn_started(run_id, model_call=model_call)
                await self._hub.before_model(run_id, request)
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

                # Execute each tool call with policy check via the bus.
                for call in response.tool_calls:
                    if is_cancelled(cancellation):
                        return await self._hub.finish_cancelled(state)

                    try:
                        outcome = await self._tool_step.run_with_policy(
                            run_id,
                            state,
                            sandbox,
                            call,
                            cancellation,
                        )
                    except ToolRunCancelled:
                        return await self._hub.finish_cancelled(state)

                    if isinstance(outcome, RunResult):
                        return outcome

                    state = append_tool_result(state, call, outcome)
                    await self._hub.after_tool(run_id, call, outcome, state)

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
