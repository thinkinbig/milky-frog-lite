from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from pathlib import Path

from pydantic import JsonValue

from milky_frog.domain import (
    HandlerResult,
    ModelRequest,
    ModelResponse,
    ReasoningDelta,
    RunRequest,
    RunResult,
    RunState,
    RunStatus,
    TextDelta,
    TokenUsage,
    ToolCall,
    ToolResult,
)
from milky_frog.events.events import (
    BaseEvent,
    NoticeLevel,
    RunAfterModel,
    RunAfterTool,
    RunBeforeModel,
    RunBeforeResume,
    RunBeforeStart,
    RunBeforeTool,
    RunCancelled,
    RunCompaction,
    RunCompleted,
    RunFailed,
    RunModelChunk,
    RunModelReasoning,
    RunNotice,
    RunPaused,
    RunStarted,
    RunTurnEnd,
    RunTurnStart,
)

_BroadcastFn = Callable[[BaseEvent], Awaitable[list[HandlerResult]]]


class RunEmitter:
    """Publish lifecycle signals and terminal Run outcomes.

    ``EventHub`` delegates its Harness-facing publish API here so subscription
    registry and Run emission stay separate modules (ADR-0012 ``RunEmitter``).
    """

    def __init__(self, broadcast: _BroadcastFn) -> None:
        self._broadcast = broadcast

    async def run_before_start(self, run_id: str, request: RunRequest, workspace: Path) -> None:
        await self._broadcast(
            RunBeforeStart(run_id=run_id, request=deepcopy(request), workspace=workspace)
        )

    async def run_started(
        self, run_id: str, request: RunRequest, state: RunState
    ) -> list[HandlerResult]:
        return await self._broadcast(
            RunStarted(run_id=run_id, request=deepcopy(request), state=state)
        )

    async def before_resume(
        self, run_id: str, prompt: str | None, status: RunStatus, workspace: Path
    ) -> list[HandlerResult]:
        return await self._broadcast(
            RunBeforeResume(run_id=run_id, prompt=prompt, stored_status=status, workspace=workspace)
        )

    async def before_model(
        self, run_id: str, request: ModelRequest, state: RunState
    ) -> list[HandlerResult]:
        return await self._broadcast(
            RunBeforeModel(run_id=run_id, request=deepcopy(request), state=state)
        )

    async def on_model_chunk(
        self, run_id: str, request: ModelRequest, chunk: TextDelta
    ) -> list[HandlerResult]:
        return await self._broadcast(
            RunModelChunk(run_id=run_id, request=deepcopy(request), chunk=chunk)
        )

    async def on_model_reasoning(
        self, run_id: str, request: ModelRequest, chunk: ReasoningDelta
    ) -> list[HandlerResult]:
        return await self._broadcast(
            RunModelReasoning(run_id=run_id, request=deepcopy(request), chunk=chunk)
        )

    async def after_model(
        self, run_id: str, request: ModelRequest, response: ModelResponse, state: RunState
    ) -> list[HandlerResult]:
        return await self._broadcast(
            RunAfterModel(
                run_id=run_id,
                request=deepcopy(request),
                response=deepcopy(response),
                state=state,
            )
        )

    async def before_tool(self, run_id: str, call: ToolCall) -> None:
        await self._broadcast(
            RunBeforeTool(
                run_id=run_id,
                call=ToolCall(call.id, call.name, deepcopy(call.arguments)),
            )
        )

    async def after_tool(
        self, run_id: str, call: ToolCall, result: ToolResult, state: RunState
    ) -> list[HandlerResult]:
        return await self._broadcast(
            RunAfterTool(
                run_id=run_id,
                call=ToolCall(call.id, call.name, deepcopy(call.arguments)),
                result=result,
                state=state,
            )
        )

    async def turn_started(self, run_id: str, model_call: int) -> list[HandlerResult]:
        return await self._broadcast(RunTurnStart(run_id=run_id, model_call=model_call))

    async def turn_ended(self, run_id: str, model_call: int) -> list[HandlerResult]:
        return await self._broadcast(RunTurnEnd(run_id=run_id, model_call=model_call))

    async def run_notice(
        self, run_id: str, message: str, *, level: NoticeLevel = "info"
    ) -> list[HandlerResult]:
        return await self._broadcast(RunNotice(run_id=run_id, message=message, level=level))

    async def run_compaction(
        self, run_id: str, messages_folded: int, usage: TokenUsage
    ) -> list[HandlerResult]:
        return await self._broadcast(
            RunCompaction(run_id=run_id, messages_folded=messages_folded, usage=usage)
        )

    async def run_completed(self, state: RunState, result: RunResult) -> list[HandlerResult]:
        return await self._broadcast(RunCompleted(run_id=state.run_id, result=result, state=state))

    async def run_paused(self, state: RunState, result: RunResult) -> list[HandlerResult]:
        return await self._broadcast(RunPaused(run_id=state.run_id, result=result, state=state))

    async def run_cancelled(self, state: RunState, result: RunResult) -> list[HandlerResult]:
        return await self._broadcast(RunCancelled(run_id=state.run_id, result=result, state=state))

    async def run_failed(self, state: RunState, result: RunResult) -> list[HandlerResult]:
        return await self._broadcast(RunFailed(run_id=state.run_id, result=result, state=state))

    async def finish_failed(self, state: RunState, error: Exception) -> RunResult:
        result = RunResult(
            state.run_id,
            RunStatus.FAILED,
            _format_failure_message(error),
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

    async def finish_approval_needed(
        self, state: RunState, calls: tuple[ToolCall, ...]
    ) -> RunResult:
        """Halt the Run for approval, exposing every call in ``calls``.

        ``unmatched_tool_calls`` on the persisted transcript is the source of
        truth for *which* calls are pending; the display message only
        describes the first one. The Harness resolves calls one at a time
        via ``respond_approval``, re-halting until the batch is exhausted.
        """
        result = RunResult(
            state.run_id,
            RunStatus.WAITING_FOR_APPROVAL,
            format_approval_message(calls[0]),
            state.completed_model_calls,
            state.usage,
        )
        await self.run_paused(state, result)
        return result


def format_approval_message(call: ToolCall) -> str:
    """Build a user-facing message for a tool call that needs approval."""
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

    if tool_name == "merge_worktree":
        branch = call.arguments.get("branch", "")
        worktree = call.arguments.get("worktree", "")
        return (
            "approval needed for: merge_worktree"
            f"\n\nA subagent left changes on branch '{branch}' at '{worktree}'."
            " Merge them into your workspace and remove the worktree?"
        )

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
    parts: list[str] = []
    for key in ("path", "pattern", "target", "url", "command", "prompt"):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            parts.append(f"{key}: '{value}'")
            if len(parts) >= 2:
                break
    if parts:
        return "(" + ", ".join(parts) + ")"
    return ""


def _format_failure_message(error: BaseException) -> str:
    """Render ``error`` into the ``final_message`` string stored on a failed Run.

    The default ``f"{type(error).__name__}: {error}"`` is useless for exceptions
    whose ``str()`` is empty (notably bare ``httpx.ReadTimeout`` raised mid
    stream, whose tail we see as ``"ReadTimeout: "``). Pull a useful identifier
    out of the request object when available.
    """
    base = f"{type(error).__name__}: {error}"
    if str(error):
        return base
    try:
        import httpx

        request: httpx.Request | None = getattr(error, "request", None)
    except ImportError:
        request = None
    if request is not None and request.url:
        return f"{type(error).__name__}: upstream {request.url.host}{request.url.path}"
    return base
