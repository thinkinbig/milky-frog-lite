from __future__ import annotations

from copy import deepcopy
from pathlib import Path

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
    HandlerResult,
    LifecycleBus,
    RunAfterModel,
    RunAfterTool,
    RunBeforeModel,
    RunBeforeResume,
    RunBeforeStart,
    RunBeforeTool,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunModelChunk,
    RunModelReasoning,
    RunNotice,
    RunPaused,
    RunStarted,
    RunTurnEnd,
    RunTurnStart,
    SystemPromptSection,
)
from milky_frog.handlers.events import NoticeLevel


class RunEmitter:
    """Dispatches Harness lifecycle signals to the LifecycleBus.

    Every event is dispatched through the ``LifecycleBus``.  The bus
    may have subscribers that render (TUI), observe (Langfuse),
    persist (CheckpointHandler), or control (PolicyHandler) — the
    emitter doesn't know or care which.
    """

    def __init__(self, handlers: LifecycleBus) -> None:
        self._handlers = handlers

    # ── Run lifecycle ──────────────────────────────────────────────────────

    async def run_before_start(
        self, run_id: str, request: RunRequest, workspace: Path
    ) -> tuple[str, ...]:
        """Dispatch ``RunBeforeStart`` and collect ``SystemPromptSection`` results.

        Handlers (e.g. Skills) may return ``SystemPromptSection`` to inject
        content into the system prompt.  Returns the collected section strings
        in handler-registration order for ``start_run`` to append.
        """
        results = await self._handlers.notify(
            RunBeforeStart(run_id=run_id, request=deepcopy(request), workspace=workspace)
        )
        return tuple(r.content for r in results if isinstance(r, SystemPromptSection))

    async def run_started(
        self, run_id: str, request: RunRequest, state: RunState
    ) -> list[HandlerResult]:
        return await self._handlers.notify(
            RunStarted(run_id=run_id, request=deepcopy(request), state=state)
        )

    async def before_resume(
        self, run_id: str, prompt: str | None, status: RunStatus
    ) -> list[HandlerResult]:
        """Notify handlers before a Run is prepared for resumption."""
        return await self._handlers.notify(
            RunBeforeResume(run_id=run_id, prompt=prompt, stored_status=status)
        )

    async def before_model(self, run_id: str, request: ModelRequest) -> list[HandlerResult]:
        return await self._handlers.notify(RunBeforeModel(run_id=run_id, request=deepcopy(request)))

    async def on_model_chunk(
        self, run_id: str, request: ModelRequest, chunk: TextDelta
    ) -> list[HandlerResult]:
        return await self._handlers.notify(
            RunModelChunk(run_id=run_id, request=deepcopy(request), chunk=chunk)
        )

    async def on_model_reasoning(
        self, run_id: str, request: ModelRequest, chunk: ReasoningDelta
    ) -> list[HandlerResult]:
        return await self._handlers.notify(
            RunModelReasoning(run_id=run_id, request=deepcopy(request), chunk=chunk)
        )

    async def after_model(
        self, run_id: str, request: ModelRequest, response: ModelResponse, state: RunState
    ) -> list[HandlerResult]:
        return await self._handlers.notify(
            RunAfterModel(
                run_id=run_id,
                request=deepcopy(request),
                response=deepcopy(response),
                state=state,
            )
        )

    async def before_tool(self, run_id: str, call: ToolCall) -> list[HandlerResult]:
        """Dispatch ``RunBeforeTool`` and return handler results.

        Handlers return ``BlockResult`` to deny the call or
        ``ApprovalResult`` to pause for approval.  The Harness checks
        these results after dispatch — no separate control path needed.
        """
        return await self._handlers.notify(
            RunBeforeTool(
                run_id=run_id,
                call=ToolCall(call.id, call.name, deepcopy(call.arguments)),
            )
        )

    async def after_tool(
        self, run_id: str, call: ToolCall, result: ToolResult, state: RunState
    ) -> list[HandlerResult]:
        return await self._handlers.notify(
            RunAfterTool(
                run_id=run_id,
                call=ToolCall(call.id, call.name, deepcopy(call.arguments)),
                result=result,
                state=state,
            )
        )

    async def turn_started(self, run_id: str, model_call: int) -> list[HandlerResult]:
        return await self._handlers.notify(RunTurnStart(run_id=run_id, model_call=model_call))

    async def turn_ended(self, run_id: str, model_call: int) -> list[HandlerResult]:
        return await self._handlers.notify(RunTurnEnd(run_id=run_id, model_call=model_call))

    async def run_notice(
        self, run_id: str, message: str, *, level: NoticeLevel = "info"
    ) -> list[HandlerResult]:
        """Dispatch an ephemeral user-facing Run notice (retry toast, warning, …)."""
        return await self._handlers.notify(
            RunNotice(run_id=run_id, message=message, level=level)
        )

    # ── Terminal Run flows ──────────────────────────────────────────────────

    async def run_completed(self, state: RunState, result: RunResult) -> list[HandlerResult]:
        return await self._handlers.notify(
            RunCompleted(run_id=state.run_id, result=result, state=state)
        )

    async def run_paused(self, state: RunState, message: str) -> list[HandlerResult]:
        return await self._handlers.notify(
            RunPaused(
                run_id=state.run_id,
                status=RunStatus.PAUSED_LIMIT,
                reason=message,
                model_calls=state.completed_model_calls,
                state=state,
            )
        )

    async def run_cancelled(self, state: RunState, reason: str) -> list[HandlerResult]:
        return await self._handlers.notify(
            RunCancelled(
                run_id=state.run_id,
                reason=reason,
                model_calls=state.completed_model_calls,
                state=state,
            )
        )

    async def run_failed(self, state: RunState, error: Exception) -> list[HandlerResult]:
        return await self._handlers.notify(RunFailed(run_id=state.run_id, error=error, state=state))

    async def finish_completed(self, state: RunState, final_message: str) -> RunResult:
        result = RunResult(
            state.run_id,
            RunStatus.COMPLETED,
            final_message,
            state.completed_model_calls,
            state.usage,
        )
        await self.run_completed(state, result)
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
        await self.run_paused(state, message)
        return result

    async def finish_cancelled(self, state: RunState, reason: str = "cancelled") -> RunResult:
        result = RunResult(
            state.run_id,
            RunStatus.CANCELLED,
            reason,
            state.completed_model_calls,
            state.usage,
        )
        await self.run_cancelled(state, reason)
        return result

    async def finish_approval_needed(self, state: RunState, tool_names: list[str]) -> RunResult:
        message = f"approval needed for: {', '.join(tool_names)}"
        result = RunResult(
            state.run_id,
            RunStatus.WAITING_FOR_APPROVAL,
            message,
            state.completed_model_calls,
            state.usage,
        )
        await self._handlers.notify(
            RunPaused(
                run_id=state.run_id,
                status=RunStatus.WAITING_FOR_APPROVAL,
                reason=message,
                model_calls=state.completed_model_calls,
                state=state,
            )
        )
        return result
