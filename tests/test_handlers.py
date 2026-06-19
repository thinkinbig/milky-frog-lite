from dataclasses import dataclass

import pytest

from milky_frog.handlers.registry import HandlerRegistry


@dataclass
class Event:
    value: str


@pytest.mark.asyncio
async def test_handlers_run_by_priority_then_registration_order() -> None:
    registry = HandlerRegistry()
    calls: list[str] = []

    @registry.on(Event, priority=10)
    async def first_high_priority(event: Event) -> None:
        calls.append(f"first:{event.value}")

    @registry.on(Event, priority=10)
    async def second_high_priority(event: Event) -> None:
        calls.append(f"second:{event.value}")

    @registry.on(Event)
    async def low_priority(event: Event) -> None:
        calls.append(f"low:{event.value}")

    await registry.dispatch(Event("value"))

    assert calls == ["first:value", "second:value", "low:value"]
