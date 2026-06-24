from dataclasses import dataclass

import pytest

from milky_frog.domain import ToolCall
from milky_frog.events.events import BaseEvent, RunBeforeTool, RunNotice
from milky_frog.events.hub import EventHub


@dataclass(frozen=True)
class SampleEvent(BaseEvent):
    value: str


@pytest.mark.asyncio
async def test_observe_handlers_run_by_priority_then_registration_order() -> None:
    registry = EventHub()
    calls: list[str] = []

    @registry.observe(SampleEvent, priority=10)
    async def first_high_priority(event: SampleEvent, _ctx=None) -> None:
        calls.append(f"first:{event.value}")

    @registry.observe(SampleEvent, priority=10)
    async def second_high_priority(event: SampleEvent, _ctx=None) -> None:
        calls.append(f"second:{event.value}")

    @registry.observe(SampleEvent)
    async def low_priority(event: SampleEvent, _ctx=None) -> None:
        calls.append(f"low:{event.value}")

    await registry.broadcast(SampleEvent(run_id="test", value="value"))

    assert calls == ["first:value", "second:value", "low:value"]


@pytest.mark.asyncio
async def test_on_registers_observe_handlers() -> None:
    registry = EventHub()
    calls: list[str] = []

    @registry.on(SampleEvent)
    async def record(event: SampleEvent, _ctx=None) -> None:
        calls.append(event.value)

    await registry.broadcast(SampleEvent(run_id="test", value="legacy"))

    assert calls == ["legacy"]


@pytest.mark.asyncio
async def test_subscribe_receives_every_notified_signal() -> None:
    registry = EventHub()
    seen: list[str] = []

    @registry.subscribe
    async def record(event: BaseEvent, _ctx=None) -> None:
        seen.append(type(event).__name__)

    await registry.broadcast(RunNotice(run_id="test", message="one"))
    await registry.broadcast(RunBeforeTool(run_id="run", call=ToolCall("id", "echo", {})))

    assert seen == ["RunNotice", "RunBeforeTool"]


@pytest.mark.asyncio
async def test_subscribe_runs_by_priority_with_typed_observe_handlers() -> None:
    registry = EventHub()
    calls: list[str] = []

    async def wildcard_first(_event: BaseEvent, _ctx=None) -> None:
        calls.append("wildcard")

    registry.subscribe(wildcard_first, priority=10)

    @registry.observe(RunNotice)
    async def typed(_event: RunNotice, _ctx=None) -> None:
        calls.append("typed")

    await registry.broadcast(RunNotice(run_id="test", message="value"))

    assert calls == ["wildcard", "typed"]
