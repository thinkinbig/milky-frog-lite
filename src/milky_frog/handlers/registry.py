from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar, cast

EventT = TypeVar("EventT")
Handler = Callable[[Any], Awaitable[None]]
TypedHandler = Callable[[EventT], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class _Registration:
    priority: int
    order: int
    handler: Handler


class HandlerRegistry:
    """Instance-owned, deterministic lifecycle Handler registry."""

    def __init__(self) -> None:
        self._registrations: dict[type[object], list[_Registration]] = defaultdict(list)
        self._next_order = 0

    def on(
        self, event_type: type[EventT], *, priority: int = 0
    ) -> Callable[[TypedHandler[EventT]], TypedHandler[EventT]]:
        def register(handler: TypedHandler[EventT]) -> TypedHandler[EventT]:
            registration = _Registration(priority, self._next_order, cast(Handler, handler))
            self._next_order += 1
            self._registrations[event_type].append(registration)
            return handler

        return register

    async def dispatch(self, event: object) -> None:
        registrations = sorted(
            self._registrations[type(event)], key=lambda item: (-item.priority, item.order)
        )
        for registration in registrations:
            await registration.handler(event)
