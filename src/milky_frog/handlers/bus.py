from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar, cast

from milky_frog.handlers.events import BaseEvent

EventT = TypeVar("EventT", bound=BaseEvent)
NotifyHandler = Callable[[EventT], Awaitable[None]]
Handler = Callable[[Any], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class _Registration:
    priority: int
    order: int
    handler: Handler


class _RegistrationSortKey:
    def __call__(self, registration: _Registration) -> tuple[int, int]:
        return (-registration.priority, registration.order)


class LifecycleBus:
    """Instance-owned read-only notification bus for Harness lifecycle signals.

    Handlers registered via ``observe`` (or ``on`` / ``subscribe``) may inspect
    signals only; they cannot change Harness execution. Checkpoint events are
    separate — see ``milky_frog.harness.state``.
    """

    def __init__(self) -> None:
        self._observe: dict[type[object], list[_Registration]] = defaultdict(list)
        self._wildcard_observe: list[_Registration] = []
        self._next_order = 0

    def observe(
        self, event_type: type[EventT], *, priority: int = 0
    ) -> Callable[[NotifyHandler[EventT]], NotifyHandler[EventT]]:
        """Register a read-only Handler for one lifecycle signal type."""

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

    async def notify(self, event: BaseEvent) -> None:
        """Deliver a lifecycle signal to every matching observe Handler."""
        registrations = list(self._observe[type(event)])
        registrations.extend(self._wildcard_observe)
        for registration in self._sorted(registrations):
            await registration.handler(event)

    def _registration(self, priority: int, handler: Callable[..., Any]) -> _Registration:
        registration = _Registration(priority, self._next_order, cast(Handler, handler))
        self._next_order += 1
        return registration

    @staticmethod
    def _sorted(registrations: list[_Registration]) -> list[_Registration]:
        return sorted(registrations, key=_RegistrationSortKey())


class BaseHandler(ABC):
    """A cross-cutting bundle of Handlers with an optional resource lifetime.

    A bundle wires several callbacks onto a LifecycleBus in one place (its
    own file) via ``register``. Bundles that hold resources for the process's
    lifetime (clients, connections) override ``aclose``; the rest inherit the
    no-op default so the runtime can release every bundle uniformly.
    """

    @abstractmethod
    def register(self, registry: LifecycleBus) -> None:
        """Wire this bundle's callbacks onto the registry."""

    async def aclose(self) -> None:  # noqa: B027 - intentional no-op default; resource-holding bundles override
        """Release resources held for the bundle's lifetime. Default: no-op."""
