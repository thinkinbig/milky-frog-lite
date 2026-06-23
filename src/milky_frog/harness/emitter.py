from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from pydantic import JsonValue

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
    EventDispatcher,
    HandlerResult,
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


def _format_approval_message(call: ToolCall) -> str:
    """Build a user-facing message for a tool call that needs approval.

    Shows the tool name and a concise preview of its arguments,
    matching the rich context pattern from pi's permission system.
    """
    tool_name = call.name

    if tool_name == "bash":
        command = call.arguments.get("command", "")
        if command:
            return (
                "approval needed for: bash"
                f"\n\nAgent requested bash command '{command}'."
                " Allow this command?"
            )
        return "approval needed for: bash\n\nAllow this bash command?"

    if tool_name == "read":
        path = call.arguments.get("path", "")
        if path:
            return (
                f"approval needed for: read\n\nAgent requested to read '{path}'. Allow this read?"
            )
        return "approval needed for: read\n\nAllow this file read?"

    if tool_name == "write":
        path = call.arguments.get("path", "")
        if path:
            return (
                "approval needed for: write"
                f"\n\nAgent requested to write to '{path}'."
                " Allow this write?"
            )
        return "approval needed for: write\n\nAllow this file write?"

    if tool_name == "edit":
        path = call.arguments.get("path", "")
        if path:
            return (
                f"approval needed for: edit\n\nAgent requested to edit '{path}'. Allow this edit?"
            )
        return "approval needed for: edit\n\nAllow this edit?"

    # Generic tool: show first few string arguments as preview.
    preview = _tool_arg_preview(call.arguments)
    if preview:
        return (
            f"approval needed for: {tool_name}"
            f"\n\nAgent requested tool '{tool_name}' {preview}."
            " Allow this call?"
        )
    return (
        f"approval needed for: {tool_name}\n\nAgent requested tool '{tool_name}'. Allow this call?"
    )


def _tool_arg_preview(arguments: dict[str, JsonValue]) -> str:
    """Return a compact preview of the first few relevant arguments."""
    parts: list[str] = []
    for key in ("path", "pattern", "target", "url", "command"):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            parts.append(f"{key}: '{value}'")
            if len(parts) >= 2:
                break
    if parts:
        return "(" + ", ".join(parts) + ")"
    return ""


class RunEmitter:
    """Dispatches Harness lifecycle signals to the EventDispatcher.

    Every event is dispatched through the ``EventDispatcher``.  The dispatcher
    may have subscribers that render (TUI), observe (Langfuse),
    persist (CheckpointHandler), or control (PolicyHandler) — the
    emitter doesn't know or care which.
    """

    def __init__(self, handlers: EventDispatcher) -> None:
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
        return await self._handlers.notify(RunNotice(run_id=run_id, message=message, level=level))

    # ── Terminal Run flows ──────────────────────────────────────────────────

    async def run_completed(self, state: RunState, result: RunResult) -> list[HandlerResult]:
        return await self._handlers.notify(
            RunCompleted(run_id=state.run_id, result=result, state=state)
        )

    async def run_paused(self, state: RunState, result: RunResult) -> list[HandlerResult]:
        return await self._handlers.notify(
            RunPaused(run_id=state.run_id, result=result, state=state)
        )

    async def run_cancelled(self, state: RunState, result: RunResult) -> list[HandlerResult]:
        return await self._handlers.notify(
            RunCancelled(run_id=state.run_id, result=result, state=state)
        )

    async def run_failed(self, state: RunState, result: RunResult) -> list[HandlerResult]:
        return await self._handlers.notify(
            RunFailed(run_id=state.run_id, result=result, state=state)
        )

    async def finish_failed(self, state: RunState, error: Exception) -> RunResult:
        result = RunResult(
            state.run_id,
            RunStatus.FAILED,
            f"{type(error).__name__}: {error}",
            state.completed_model_calls,
            state.usage,
        )
        await self.run_failed(state, result)
        return result

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
        await self.run_paused(state, result)
        return result

    async def finish_cancelled(self, state: RunState, reason: str = "cancelled") -> RunResult:
        result = RunResult(
            state.run_id,
            RunStatus.CANCELLED,
            reason,
            state.completed_model_calls,
            state.usage,
        )
        await self.run_cancelled(state, result)
        return result

    async def finish_approval_needed(self, state: RunState, call: ToolCall) -> RunResult:
        message = _format_approval_message(call)
        result = RunResult(
            state.run_id,
            RunStatus.WAITING_FOR_APPROVAL,
            message,
            state.completed_model_calls,
            state.usage,
        )
        await self.run_paused(state, result)
        return result
