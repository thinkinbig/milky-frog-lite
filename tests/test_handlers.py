import pytest

from milky_frog.domain import ToolCall
from milky_frog.handlers.base import BaseEvent
from milky_frog.handlers.events import BeforeTool
from milky_frog.handlers.registry import HandlerRegistry


class SampleEvent(BaseEvent):
    run_id: str = "test"
    value: str


@pytest.mark.asyncio
async def test_observe_handlers_run_by_priority_then_registration_order() -> None:
    registry = HandlerRegistry()
    calls: list[str] = []

    @registry.observe(SampleEvent, priority=10)
    async def first_high_priority(event: SampleEvent) -> None:
        calls.append(f"first:{event.value}")

    @registry.observe(SampleEvent, priority=10)
    async def second_high_priority(event: SampleEvent) -> None:
        calls.append(f"second:{event.value}")

    @registry.observe(SampleEvent)
    async def low_priority(event: SampleEvent) -> None:
        calls.append(f"low:{event.value}")

    await registry.notify(SampleEvent(value="value"))

    assert calls == ["first:value", "second:value", "low:value"]


@pytest.mark.asyncio
async def test_on_registers_observe_handlers() -> None:
    registry = HandlerRegistry()
    calls: list[str] = []

    @registry.on(SampleEvent)
    async def record(event: SampleEvent) -> None:
        calls.append(event.value)

    await registry.notify(SampleEvent(value="legacy"))

    assert calls == ["legacy"]


@pytest.mark.asyncio
async def test_subscribe_receives_every_notified_signal() -> None:
    registry = HandlerRegistry()
    seen: list[str] = []

    @registry.subscribe
    async def record(event: BaseEvent) -> None:
        seen.append(type(event).__name__)

    await registry.notify(SampleEvent(value="one"))
    await registry.notify(BeforeTool(run_id="run", call=ToolCall("id", "echo", {})))

    assert seen == ["SampleEvent", "BeforeTool"]


@pytest.mark.asyncio
async def test_subscribe_runs_by_priority_with_typed_observe_handlers() -> None:
    registry = HandlerRegistry()
    calls: list[str] = []

    async def wildcard_first(_event: BaseEvent) -> None:
        calls.append("wildcard")

    registry.subscribe(wildcard_first, priority=10)

    @registry.observe(SampleEvent)
    async def typed(_event: SampleEvent) -> None:
        calls.append("typed")

    await registry.notify(SampleEvent(value="value"))

    assert calls == ["wildcard", "typed"]
