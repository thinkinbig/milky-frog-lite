from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Self, TypeVar, cast

from milky_frog.core.handlers import HandlerDeps
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
from milky_frog.events.emitter import RunEmitter
from milky_frog.events.events import (
    LIFECYCLE_EVENT_TYPES,
    BaseEvent,
    NoticeLevel,
)

EventT = TypeVar("EventT", bound=BaseEvent)
BroadcastHandler = Callable[[EventT, HandlerDeps], Awaitable[HandlerResult | None]]
HandlerCallback = Callable[[Any, HandlerDeps], Awaitable[HandlerResult | None]]

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _Registration:
    priority: int
    order: int
    handler: HandlerCallback


class EventHub:
    """Harness lifecycle hub: Handlers subscribe; the Harness broadcasts.

    Handlers subscribe via ``observe``, ``on``, or ``subscribe``. A callback
    returns ``None`` to observe, or a ``HandlerResult`` to propose a change the
    loop applies at the relevant control point. ``AgentLoop`` and
    ``AgentHarness`` publish through the typed emit methods below.
    """

    def __init__(self) -> None:
        self._observe: dict[type[object], list[_Registration]] = defaultdict(list)
        self._next_order = 0
        self._deps: HandlerDeps = HandlerDeps()
        self._emitter = RunEmitter(self.broadcast)

    def observe(
        self, event_type: type[EventT], *, priority: int = 0
    ) -> Callable[[BroadcastHandler[EventT]], BroadcastHandler[EventT]]:
        """Register an Observer callback for one lifecycle signal type."""

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

    def set_deps(self, deps: HandlerDeps) -> None:
        """Set stable Handler dependencies for every subsequent ``broadcast``."""
        self._deps = deps

    async def broadcast(self, event: BaseEvent) -> list[HandlerResult]:
        """Deliver a lifecycle signal to every matching Handler callback.

        Each callback receives the event together with the shared
        ``HandlerDeps`` set via ``set_deps``. Per-Run facts belong on the
        lifecycle signal itself. Non-``None`` returns are ``HandlerResult``
        proposals, collected and returned in delivery order — the caller
        (the loop) decides how to apply them.
        """
        registrations = list(self._observe[type(event)])
        deps = self._deps
        results: list[HandlerResult] = []
        for registration in self._sorted(registrations):
            result = await registration.handler(event, deps)
            if result is not None:
                results.append(result)
        return results

    def _registration(self, priority: int, handler: Callable[..., Any]) -> _Registration:
        registration = _Registration(priority, self._next_order, cast(HandlerCallback, handler))
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

    async def before_model(
        self, run_id: str, request: ModelRequest, state: RunState
    ) -> list[HandlerResult]:
        return await self._emitter.before_model(run_id, request, state)

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

    async def run_compaction(
        self, run_id: str, messages_folded: int, usage: TokenUsage
    ) -> list[HandlerResult]:
        return await self._emitter.run_compaction(run_id, messages_folded, usage)

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

    async def finish_approval_needed(
        self, state: RunState, calls: tuple[ToolCall, ...]
    ) -> RunResult:
        return await self._emitter.finish_approval_needed(state, calls)


class Handler:
    """A lifecycle Handler: wires callbacks onto the hub, with optional lifetime.

    Subclasses implement ``register`` to attach callbacks (via ``observe`` /
    ``on``). A callback returns ``None`` to observe, or a ``HandlerResult`` to
    propose a change the loop applies — the observe-vs-control distinction is the
    callback's *return*, not a separate type. ``aclose`` releases any resources
    held for the Handler's lifetime (default no-op); the runtime enters each
    Handler with ``async with`` and closes it when the session ends.
    """

    def register(self, hub: EventHub) -> None:
        """Wire this Handler's callbacks onto the hub."""
        raise NotImplementedError

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

    async def aclose(self) -> None:
        """Release resources held for the Handler's lifetime. Default: no-op."""
