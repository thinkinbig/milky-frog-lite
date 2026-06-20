from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar, cast

from milky_frog.domain import ModelRequest, ToolResult
from milky_frog.handlers.base import BaseEvent
from milky_frog.handlers.events import AfterTool, BeforeModel, BeforeTool
from milky_frog.handlers.results import BlockTool, PatchToolResult, TransformContext

EventT = TypeVar("EventT", bound=BaseEvent)
ObserveHandler = Callable[[EventT], Awaitable[None]]
InterceptHandler = Callable[[EventT], Awaitable[object | None]]
Handler = Callable[[Any], Awaitable[object | None]]

InterceptOutcome = BlockTool | TransformContext | PatchToolResult


@dataclass(frozen=True, slots=True)
class _Registration:
    priority: int
    order: int
    handler: Handler


class HandlerRegistry:
    """Instance-owned Handler registry with separate observe and intercept channels.

    ``observe`` handlers (and ``on`` / ``subscribe``) may inspect events only.
    ``intercept`` handlers may return typed outcomes that the Harness applies.
    """

    def __init__(self) -> None:
        self._observe: dict[type[object], list[_Registration]] = defaultdict(list)
        self._intercept: dict[type[object], list[_Registration]] = defaultdict(list)
        self._wildcard_observe: list[_Registration] = []
        self._next_order = 0

    def observe(
        self, event_type: type[EventT], *, priority: int = 0
    ) -> Callable[[ObserveHandler[EventT]], ObserveHandler[EventT]]:
        """Register a read-only Handler for one lifecycle event type."""

        def register(handler: ObserveHandler[EventT]) -> ObserveHandler[EventT]:
            self._observe[event_type].append(self._registration(priority, handler))
            return handler

        return register

    def intercept(
        self, event_type: type[EventT], *, priority: int = 0
    ) -> Callable[[InterceptHandler[EventT]], InterceptHandler[EventT]]:
        """Register a Handler that may return an intercept outcome for the Harness."""

        def register(handler: InterceptHandler[EventT]) -> InterceptHandler[EventT]:
            self._intercept[event_type].append(self._registration(priority, handler))
            return handler

        return register

    def on(
        self, event_type: type[EventT], *, priority: int = 0
    ) -> Callable[[ObserveHandler[EventT]], ObserveHandler[EventT]]:
        """Register an observe Handler (backward-compatible alias for ``observe``)."""
        return self.observe(event_type, priority=priority)

    def subscribe(
        self, handler: ObserveHandler[BaseEvent], *, priority: int = 0
    ) -> ObserveHandler[BaseEvent]:
        """Register a wildcard observe Handler that receives every lifecycle event."""
        self._wildcard_observe.append(self._registration(priority, handler))
        return handler

    async def dispatch(self, event: BaseEvent) -> InterceptOutcome | None:
        """Run intercept Handlers, apply outcomes, observe Handlers, return intercept outcome."""
        outcome = await self._dispatch_intercept(event)
        self._apply_intercept_outcome(event, outcome)
        await self._dispatch_observe(event)
        return outcome

    def _apply_intercept_outcome(self, event: BaseEvent, outcome: InterceptOutcome | None) -> None:
        if outcome is None:
            return
        if isinstance(event, BeforeModel) and isinstance(outcome, TransformContext):
            event.request = ModelRequest(outcome.messages, event.request.tools)
        if isinstance(event, AfterTool) and isinstance(outcome, PatchToolResult):
            event.result = ToolResult(
                outcome.content if outcome.content is not None else event.result.content,
                is_error=(
                    outcome.is_error if outcome.is_error is not None else event.result.is_error
                ),
            )

    def _registration(self, priority: int, handler: Callable[..., Any]) -> _Registration:
        registration = _Registration(priority, self._next_order, cast(Handler, handler))
        self._next_order += 1
        return registration

    async def _dispatch_observe(self, event: BaseEvent) -> None:
        for registration in self._sorted(self._observe[type(event)]):
            await registration.handler(event)
        for registration in self._sorted(self._wildcard_observe):
            await registration.handler(event)

    async def _dispatch_intercept(self, event: BaseEvent) -> InterceptOutcome | None:
        registrations = self._sorted(self._intercept[type(event)])
        if isinstance(event, BeforeTool):
            return await self._intercept_before_tool(registrations, event)
        if isinstance(event, BeforeModel):
            return await self._intercept_before_model(registrations, event)
        if isinstance(event, AfterTool):
            return await self._intercept_after_tool(registrations, event)
        for registration in registrations:
            await registration.handler(event)
        return None

    async def _intercept_before_tool(
        self, registrations: list[_Registration], event: BeforeTool
    ) -> BlockTool | None:
        for registration in registrations:
            outcome = await registration.handler(event)
            if isinstance(outcome, BlockTool):
                return outcome
        return None

    async def _intercept_before_model(
        self, registrations: list[_Registration], event: BeforeModel
    ) -> TransformContext | None:
        messages = event.request.messages
        changed = False
        for registration in registrations:
            outcome = await registration.handler(event)
            if isinstance(outcome, TransformContext):
                messages = outcome.messages
                changed = True
        if not changed:
            return None
        return TransformContext(messages)

    async def _intercept_after_tool(
        self, registrations: list[_Registration], event: AfterTool
    ) -> PatchToolResult | None:
        content = event.result.content
        is_error = event.result.is_error
        changed = False
        for registration in registrations:
            outcome = await registration.handler(event)
            if not isinstance(outcome, PatchToolResult):
                continue
            if outcome.content is not None:
                content = outcome.content
                changed = True
            if outcome.is_error is not None:
                is_error = outcome.is_error
                changed = True
        if not changed:
            return None
        return PatchToolResult(content=content, is_error=is_error)

    @staticmethod
    def _sorted(registrations: list[_Registration]) -> list[_Registration]:
        return sorted(registrations, key=lambda item: (-item.priority, item.order))
