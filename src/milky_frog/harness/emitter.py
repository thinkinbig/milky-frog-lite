from __future__ import annotations

from copy import deepcopy

from milky_frog.checkpoint import CheckpointStore
from milky_frog.domain import (
    ModelRequest,
    ModelResponse,
    ReasoningDelta,
    RunRequest,
    RunResult,
    RunState,
    RunStatus,
    TextDelta,
    ToolCall,
    ToolResult,
)
from milky_frog.handlers import (
    HandlerRegistry,
    RunAfterModel,
    RunAfterTool,
    RunBeforeModel,
    RunBeforeTool,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunModelChunk,
    RunModelReasoning,
    RunPaused,
    RunStarted,
    RunTurnEnd,
    RunTurnStart,
)


class RunEmitter:
    """Owns the Harness persistence matrix: Checkpoint snapshot vs lifecycle notify."""

    def __init__(
        self,
        checkpoints: CheckpointStore,
        handlers: HandlerRegistry,
    ) -> None:
        self._checkpoints = checkpoints
        self._handlers = handlers

    def persist(
        self,
        state: RunState,
        *,
        status: RunStatus | None = None,
        final_message: str | None = None,
    ) -> None:
        self._checkpoints.save_state(
            state.run_id,
            state,
            status=status,
            final_message=final_message,
        )

    # ── Run lifecycle ──────────────────────────────────────────────────────

    async def run_started(self, run_id: str, request: RunRequest, state: RunState) -> None:
        self.persist(state, status=RunStatus.RUNNING)
        await self._handlers.notify(RunStarted(run_id=run_id, request=deepcopy(request)))

    async def before_model(self, run_id: str, request: ModelRequest) -> None:
        await self._handlers.notify(RunBeforeModel(run_id=run_id, request=deepcopy(request)))

    async def on_model_chunk(self, run_id: str, request: ModelRequest, chunk: TextDelta) -> None:
        await self._handlers.notify(
            RunModelChunk(run_id=run_id, request=deepcopy(request), chunk=chunk)
        )

    async def on_model_reasoning(
        self, run_id: str, request: ModelRequest, chunk: ReasoningDelta
    ) -> None:
        await self._handlers.notify(
            RunModelReasoning(run_id=run_id, request=deepcopy(request), chunk=chunk)
        )

    async def after_model(
        self, run_id: str, request: ModelRequest, response: ModelResponse
    ) -> None:
        await self._handlers.notify(
            RunAfterModel(run_id=run_id, request=deepcopy(request), response=deepcopy(response))
        )

    async def before_tool(self, run_id: str, call: ToolCall) -> None:
        await self._handlers.notify(
            RunBeforeTool(
                run_id=run_id,
                call=ToolCall(call.id, call.name, deepcopy(call.arguments)),
            )
        )

    async def after_tool(self, run_id: str, call: ToolCall, result: ToolResult) -> None:
        await self._handlers.notify(
            RunAfterTool(
                run_id=run_id,
                call=ToolCall(call.id, call.name, deepcopy(call.arguments)),
                result=result,
            )
        )

    async def turn_started(self, run_id: str, model_call: int) -> None:
        await self._handlers.notify(RunTurnStart(run_id=run_id, model_call=model_call))

    async def turn_ended(self, run_id: str, model_call: int) -> None:
        await self._handlers.notify(RunTurnEnd(run_id=run_id, model_call=model_call))

    # ── Terminal Run flows ──────────────────────────────────────────────────

    async def run_completed(self, state: RunState, final_message: str, result: RunResult) -> None:
        self.persist(state, status=RunStatus.COMPLETED, final_message=final_message)
        await self._handlers.notify(RunCompleted(run_id=state.run_id, result=result))

    async def run_paused(self, state: RunState, message: str, result: RunResult) -> None:
        self.persist(state, status=RunStatus.PAUSED_LIMIT, final_message=message)
        await self._handlers.notify(
            RunPaused(
                run_id=state.run_id,
                status=RunStatus.PAUSED_LIMIT,
                reason=message,
                model_calls=state.completed_model_calls,
            )
        )

    async def run_cancelled(self, state: RunState, reason: str, result: RunResult) -> None:
        self.persist(state, status=RunStatus.CANCELLED, final_message=reason)
        await self._handlers.notify(
            RunCancelled(
                run_id=state.run_id, reason=reason, model_calls=state.completed_model_calls
            )
        )

    async def run_failed(self, state: RunState, error: Exception) -> None:
        self.persist(state, status=RunStatus.FAILED, final_message=str(error))
        await self._handlers.notify(RunFailed(run_id=state.run_id, error=error))

    async def finish_completed(self, state: RunState, final_message: str) -> RunResult:
        result = RunResult(
            state.run_id,
            RunStatus.COMPLETED,
            final_message,
            state.completed_model_calls,
            state.usage,
        )
        await self.run_completed(state, final_message, result)
        return result

    async def finish_paused(self, state: RunState, max_model_calls: int) -> RunResult:
        message = f"model call limit reached ({max_model_calls})"
        result = RunResult(
            state.run_id,
            RunStatus.PAUSED_LIMIT,
            message,
            state.completed_model_calls,
            state.usage,
        )
        await self.run_paused(state, message, result)
        return result

    async def finish_cancelled(self, state: RunState, reason: str = "cancelled") -> RunResult:
        result = RunResult(
            state.run_id,
            RunStatus.CANCELLED,
            reason,
            state.completed_model_calls,
            state.usage,
        )
        await self.run_cancelled(state, reason, result)
        return result
