from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Self, TypeVar, cast

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
from milky_frog.events.events import (
    LIFECYCLE_EVENT_TYPES,
    BaseEvent,
    NoticeLevel,
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
)
from milky_frog.handlers.context import HandlerContext, HandlerResult, SystemPromptSection

EventT = TypeVar("EventT", bound=BaseEvent)
BroadcastHandler = Callable[[EventT, HandlerContext], Awaitable[HandlerResult | None]]
Handler = Callable[[Any, HandlerContext], Awaitable[HandlerResult | None]]

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _Registration:
    priority: int
    order: int
    handler: Handler


class EventHub:
    """Harness lifecycle hub: Handlers subscribe; the Harness broadcasts.

    Handlers subscribe via ``observe``, ``on``, or ``subscribe``; most return
    ``None`` (pure observation). A few events accept control returns — see
    ``RunBeforeTool`` and ``RunBeforeStart``.  ``AgentLoop`` and
    ``AgentHarness`` publish through the typed emit methods below.
    """

    def __init__(self) -> None:
        self._observe: dict[type[object], list[_Registration]] = defaultdict(list)
        self._next_order = 0
        self._context: HandlerContext = HandlerContext()

    def observe(
        self, event_type: type[EventT], *, priority: int = 0
    ) -> Callable[[BroadcastHandler[EventT]], BroadcastHandler[EventT]]:
        """Register a Handler for one lifecycle signal type."""

        def register(handler: BroadcastHandler[EventT]) -> BroadcastHandler[EventT]:
            self._observe[event_type].append(self._registration(priority, handler))
            return handler

        return register

    def on(
        self, event_type: type[EventT], *, priority: int = 0
    ) -> Callable[[BroadcastHandler[EventT]], BroadcastHandler[EventT]]:
        """Register an observe Handler (backward-compatible alias for ``observe``)."""
        return self.observe(event_type, priority=priority)

    def subscribe(
        self, handler: BroadcastHandler[BaseEvent], *, priority: int = 0
    ) -> BroadcastHandler[BaseEvent]:
        """Register a wildcard observe Handler that receives every lifecycle signal."""
        registration = self._registration(priority, handler)
        for event_type in LIFECYCLE_EVENT_TYPES:
            self._observe[event_type].append(registration)
        return handler

    def set_context(self, ctx: HandlerContext) -> None:
        """Set the shared HandlerContext for every subsequent ``broadcast``."""
        self._context = ctx

    async def broadcast(self, event: BaseEvent) -> list[HandlerResult]:
        """Deliver a lifecycle signal to every matching observe Handler.

        Each handler receives the event together with the shared
        ``HandlerContext`` set via ``set_context``.  Non-``None`` return
        values are collected and returned — the caller (typically the Harness)
        decides whether to act on them.
        """
        registrations = list(self._observe[type(event)])
        ctx = self._context
        results: list[HandlerResult] = []
        for registration in self._sorted(registrations):
            result = await registration.handler(event, ctx)
            if result is not None:
                results.append(result)
        return results

    def _registration(self, priority: int, handler: Callable[..., Any]) -> _Registration:
        registration = _Registration(priority, self._next_order, cast(Handler, handler))
        self._next_order += 1
        return registration

    @staticmethod
    def _sorted(registrations: list[_Registration]) -> list[_Registration]:
        return sorted(registrations, key=lambda r: (-r.priority, r.order))

    # ── Harness publish API ────────────────────────────────────────────────

    async def run_before_start(
        self, run_id: str, request: RunRequest, workspace: Path
    ) -> tuple[str, ...]:
        """Dispatch ``RunBeforeStart`` and collect ``SystemPromptSection`` results."""
        results = await self.broadcast(
            RunBeforeStart(run_id=run_id, request=deepcopy(request), workspace=workspace)
        )
        return tuple(r.content for r in results if isinstance(r, SystemPromptSection))

    async def run_started(
        self, run_id: str, request: RunRequest, state: RunState
    ) -> list[HandlerResult]:
        return await self.broadcast(
            RunStarted(run_id=run_id, request=deepcopy(request), state=state)
        )

    async def before_resume(
        self, run_id: str, prompt: str | None, status: RunStatus, workspace: Path
    ) -> list[HandlerResult]:
        return await self.broadcast(
            RunBeforeResume(run_id=run_id, prompt=prompt, stored_status=status, workspace=workspace)
        )

    async def before_model(self, run_id: str, request: ModelRequest) -> list[HandlerResult]:
        return await self.broadcast(RunBeforeModel(run_id=run_id, request=deepcopy(request)))

    async def on_model_chunk(
        self, run_id: str, request: ModelRequest, chunk: TextDelta
    ) -> list[HandlerResult]:
        return await self.broadcast(
            RunModelChunk(run_id=run_id, request=deepcopy(request), chunk=chunk)
        )

    async def on_model_reasoning(
        self, run_id: str, request: ModelRequest, chunk: ReasoningDelta
    ) -> list[HandlerResult]:
        return await self.broadcast(
            RunModelReasoning(run_id=run_id, request=deepcopy(request), chunk=chunk)
        )

    async def after_model(
        self, run_id: str, request: ModelRequest, response: ModelResponse, state: RunState
    ) -> list[HandlerResult]:
        return await self.broadcast(
            RunAfterModel(
                run_id=run_id,
                request=deepcopy(request),
                response=deepcopy(response),
                state=state,
            )
        )

    async def before_tool(self, run_id: str, call: ToolCall) -> list[HandlerResult]:
        return await self.broadcast(
            RunBeforeTool(
                run_id=run_id,
                call=ToolCall(call.id, call.name, deepcopy(call.arguments)),
            )
        )

    async def after_tool(
        self, run_id: str, call: ToolCall, result: ToolResult, state: RunState
    ) -> list[HandlerResult]:
        return await self.broadcast(
            RunAfterTool(
                run_id=run_id,
                call=ToolCall(call.id, call.name, deepcopy(call.arguments)),
                result=result,
                state=state,
            )
        )

    async def turn_started(self, run_id: str, model_call: int) -> list[HandlerResult]:
        return await self.broadcast(RunTurnStart(run_id=run_id, model_call=model_call))

    async def turn_ended(self, run_id: str, model_call: int) -> list[HandlerResult]:
        return await self.broadcast(RunTurnEnd(run_id=run_id, model_call=model_call))

    async def run_notice(
        self, run_id: str, message: str, *, level: NoticeLevel = "info"
    ) -> list[HandlerResult]:
        return await self.broadcast(RunNotice(run_id=run_id, message=message, level=level))

    async def run_completed(self, state: RunState, result: RunResult) -> list[HandlerResult]:
        return await self.broadcast(RunCompleted(run_id=state.run_id, result=result, state=state))

    async def run_paused(self, state: RunState, result: RunResult) -> list[HandlerResult]:
        return await self.broadcast(RunPaused(run_id=state.run_id, result=result, state=state))

    async def run_cancelled(self, state: RunState, result: RunResult) -> list[HandlerResult]:
        return await self.broadcast(RunCancelled(run_id=state.run_id, result=result, state=state))

    async def run_failed(self, state: RunState, result: RunResult) -> list[HandlerResult]:
        return await self.broadcast(RunFailed(run_id=state.run_id, result=result, state=state))

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
        result = RunResult(
            state.run_id,
            RunStatus.WAITING_FOR_APPROVAL,
            _format_approval_message(call),
            state.completed_model_calls,
            state.usage,
        )
        await self.run_paused(state, result)
        return result


def _format_approval_message(call: ToolCall) -> str:
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
    for key in ("path", "pattern", "target", "url", "command"):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            parts.append(f"{key}: '{value}'")
            if len(parts) >= 2:
                break
    if parts:
        return "(" + ", ".join(parts) + ")"
    return ""


class BaseHandler(ABC):
    """A cross-cutting bundle of Handlers with an optional resource lifetime.

    A bundle wires several callbacks onto an ``EventHub`` in one place (its
    own file) via ``register``. Bundles that hold session resources override
    ``__aenter__`` to acquire them and ``aclose`` to release; the rest inherit
    no-op defaults. ``AgentSession`` enters every bundle when the session opens
    and exits them on close.
    """

    @abstractmethod
    def register(self, hub: EventHub) -> None:
        """Wire this bundle's callbacks onto the hub."""

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            await self.aclose()
        except Exception:
            _logger.exception("Cleanup failed: %s", type(self).__qualname__)

    async def aclose(self) -> None:  # noqa: B027 - intentional no-op default; resource-holding bundles override
        """Release resources held for the bundle's lifetime. Default: no-op."""
