from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self, TypeVar, cast

from milky_frog.handlers.context import HandlerContext, HandlerResult
from milky_frog.handlers.events import BaseEvent

EventT = TypeVar("EventT", bound=BaseEvent)
NotifyHandler = Callable[[EventT, HandlerContext], Awaitable[HandlerResult | None]]
Handler = Callable[[Any, HandlerContext], Awaitable[HandlerResult | None]]

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _Registration:
    priority: int
    order: int
    handler: Handler


class EventDispatcher:
    """Instance-owned one-to-many event dispatcher for Harness lifecycle events.

    Only ``RunEmitter`` dispatches events. Handlers subscribe via ``observe``,
    ``on``, or ``subscribe``; most return ``None`` (pure observation). A few
    events accept control returns — see ``RunBeforeTool`` and
    ``RunBeforeStart``.


    """

    def __init__(self) -> None:
        self._observe: dict[type[object], list[_Registration]] = defaultdict(list)
        self._wildcard_observe: list[_Registration] = []
        self._next_order = 0
        self._context: HandlerContext = HandlerContext()

    def observe(
        self, event_type: type[EventT], *, priority: int = 0
    ) -> Callable[[NotifyHandler[EventT]], NotifyHandler[EventT]]:
        """Register a Handler for one lifecycle signal type."""

        def register(handler: NotifyHandler[EventT]) -> NotifyHandler[EventT]:
            self._observe[event_type].append(self._registration(priority, handler))
            return handler

        return register

    def on(
        self, event_type: type[EventT], *, priority: int = 0
    ) -> Callable[[NotifyHandler[EventT]], NotifyHandler[EventT]]:
        """Register an observe Handler (backward-compatible alias for ``observe``)."""
        return self.observe(event_type, priority=priority)

    def subscribe(
        self, handler: NotifyHandler[BaseEvent], *, priority: int = 0
    ) -> NotifyHandler[BaseEvent]:
        """Register a wildcard observe Handler that receives every lifecycle signal."""
        self._wildcard_observe.append(self._registration(priority, handler))
        return handler

    def set_context(self, ctx: HandlerContext) -> None:
        """Set the shared HandlerContext for every subsequent ``notify``."""
        self._context = ctx

    async def notify(self, event: BaseEvent) -> list[HandlerResult]:
        """Deliver a lifecycle signal to every matching observe Handler.

        Each handler receives the event together with the shared
        ``HandlerContext`` set via ``set_context``.  Non-``None`` return
        values are collected and returned — the caller (typically the
        ``RunEmitter``) decides whether to act on them.
        """
        registrations = list(self._observe[type(event)])
        registrations.extend(self._wildcard_observe)
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


class BaseHandler(ABC):
    """A cross-cutting bundle of Handlers with an optional resource lifetime.

    A bundle wires several callbacks onto an EventDispatcher in one place (its
    own file) via ``register``. Bundles that hold session resources override
    ``__aenter__`` to acquire them and ``aclose`` to release; the rest inherit
    no-op defaults. ``AgentSession`` enters every bundle when the session opens
    and exits them on close.
    """

    @abstractmethod
    def register(self, registry: EventDispatcher) -> None:
        """Wire this bundle's callbacks onto the registry."""

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
