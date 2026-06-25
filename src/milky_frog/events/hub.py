from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Self, TypeVar, cast

from milky_frog.core.handlers import HandlerContext, HandlerResult
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
from milky_frog.events.emitter import RunEmitter
from milky_frog.events.events import (
    LIFECYCLE_EVENT_TYPES,
    BaseEvent,
    NoticeLevel,
)

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
        self._emitter = RunEmitter(self.broadcast)

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

    @property
    def emitter(self) -> RunEmitter:
        """Run lifecycle publisher (ADR-0012 ``RunEmitter``)."""
        return self._emitter

    # ── Harness publish API (delegates to RunEmitter) ────────────────────

    async def run_before_start(self, run_id: str, request: RunRequest, workspace: Path) -> None:
        await self._emitter.run_before_start(run_id, request, workspace)

    async def run_started(
        self, run_id: str, request: RunRequest, state: RunState
    ) -> list[HandlerResult]:
        return await self._emitter.run_started(run_id, request, state)

    async def before_resume(
        self, run_id: str, prompt: str | None, status: RunStatus, workspace: Path
    ) -> list[HandlerResult]:
        return await self._emitter.before_resume(run_id, prompt, status, workspace)

    async def before_model(self, run_id: str, request: ModelRequest) -> None:
        await self._emitter.before_model(run_id, request)

    async def on_model_chunk(
        self, run_id: str, request: ModelRequest, chunk: TextDelta
    ) -> list[HandlerResult]:
        return await self._emitter.on_model_chunk(run_id, request, chunk)

    async def on_model_reasoning(
        self, run_id: str, request: ModelRequest, chunk: ReasoningDelta
    ) -> list[HandlerResult]:
        return await self._emitter.on_model_reasoning(run_id, request, chunk)

    async def after_model(
        self, run_id: str, request: ModelRequest, response: ModelResponse, state: RunState
    ) -> list[HandlerResult]:
        return await self._emitter.after_model(run_id, request, response, state)

    async def before_tool(self, run_id: str, call: ToolCall) -> None:
        await self._emitter.before_tool(run_id, call)

    async def after_tool(
        self, run_id: str, call: ToolCall, result: ToolResult, state: RunState
    ) -> list[HandlerResult]:
        return await self._emitter.after_tool(run_id, call, result, state)

    async def turn_started(self, run_id: str, model_call: int) -> list[HandlerResult]:
        return await self._emitter.turn_started(run_id, model_call)

    async def turn_ended(self, run_id: str, model_call: int) -> list[HandlerResult]:
        return await self._emitter.turn_ended(run_id, model_call)

    async def run_notice(
        self, run_id: str, message: str, *, level: NoticeLevel = "info"
    ) -> list[HandlerResult]:
        return await self._emitter.run_notice(run_id, message, level=level)

    async def run_completed(self, state: RunState, result: RunResult) -> list[HandlerResult]:
        return await self._emitter.run_completed(state, result)

    async def run_paused(self, state: RunState, result: RunResult) -> list[HandlerResult]:
        return await self._emitter.run_paused(state, result)

    async def run_cancelled(self, state: RunState, result: RunResult) -> list[HandlerResult]:
        return await self._emitter.run_cancelled(state, result)

    async def run_failed(self, state: RunState, result: RunResult) -> list[HandlerResult]:
        return await self._emitter.run_failed(state, result)

    async def finish_failed(self, state: RunState, error: Exception) -> RunResult:
        return await self._emitter.finish_failed(state, error)

    async def finish_completed(self, state: RunState, final_message: str) -> RunResult:
        return await self._emitter.finish_completed(state, final_message)

    async def finish_paused(self, state: RunState, max_model_calls: int) -> RunResult:
        return await self._emitter.finish_paused(state, max_model_calls)

    async def finish_cancelled(self, state: RunState, reason: str = "cancelled") -> RunResult:
        return await self._emitter.finish_cancelled(state, reason)

    async def finish_approval_needed(self, state: RunState, call: ToolCall) -> RunResult:
        return await self._emitter.finish_approval_needed(state, call)


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
