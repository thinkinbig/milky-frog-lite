from pathlib import Path

import pytest

from milky_frog.domain import Message, MessageRole, ModelRequest, RunRequest, ToolCall
from milky_frog.handlers.base import BaseEvent
from milky_frog.handlers.events import AfterTool, BeforeModel, BeforeTool, RunStarted
from milky_frog.handlers.registry import HandlerRegistry
from milky_frog.handlers.results import BlockTool, PatchToolResult, TransformContext
from milky_frog.harness.tools import ToolResult


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

    await registry.dispatch(SampleEvent(value="value"))

    assert calls == ["first:value", "second:value", "low:value"]


@pytest.mark.asyncio
async def test_on_registers_observe_handlers() -> None:
    registry = HandlerRegistry()
    calls: list[str] = []

    @registry.on(SampleEvent)
    async def record(event: SampleEvent) -> None:
        calls.append(event.value)

    await registry.dispatch(SampleEvent(value="legacy"))

    assert calls == ["legacy"]


@pytest.mark.asyncio
async def test_subscribe_receives_every_dispatched_event() -> None:
    registry = HandlerRegistry()
    seen: list[str] = []

    @registry.subscribe
    async def record(event: BaseEvent) -> None:
        seen.append(type(event).__name__)

    await registry.dispatch(SampleEvent(value="one"))
    await registry.dispatch(BeforeTool(run_id="run", call=ToolCall("id", "echo", {})))

    assert seen == ["SampleEvent", "BeforeTool"]


@pytest.mark.asyncio
async def test_intercept_runs_before_observe() -> None:
    registry = HandlerRegistry()
    calls: list[str] = []

    @registry.intercept(BeforeTool)
    async def block(_event: BeforeTool) -> BlockTool:
        calls.append("intercept")
        return BlockTool("denied")

    @registry.observe(BeforeTool)
    async def observe(_event: BeforeTool) -> None:
        calls.append("observe")

    outcome = await registry.dispatch(BeforeTool(run_id="run", call=ToolCall("id", "echo", {})))

    assert isinstance(outcome, BlockTool)
    assert calls == ["intercept", "observe"]


@pytest.mark.asyncio
async def test_transform_context_updates_event_before_observe() -> None:
    registry = HandlerRegistry()
    seen_lengths: list[int] = []

    @registry.intercept(BeforeModel)
    async def transform(event: BeforeModel) -> TransformContext:
        return TransformContext((*event.request.messages, Message(MessageRole.USER, "injected")))

    @registry.observe(BeforeModel)
    async def observe(event: BeforeModel) -> None:
        seen_lengths.append(len(event.request.messages))

    original = ModelRequest((Message(MessageRole.USER, "hello"),), ())
    event = BeforeModel(run_id="run", request=original)
    await registry.dispatch(event)

    assert len(original.messages) == 1
    assert seen_lengths == [2]
    assert len(event.request.messages) == 2


@pytest.mark.asyncio
async def test_patch_tool_result_updates_event_before_observe() -> None:
    registry = HandlerRegistry()
    seen: list[str] = []

    call = ToolCall("id", "echo", {})
    original = ToolResult("raw")

    @registry.intercept(AfterTool)
    async def patch(_event: AfterTool) -> PatchToolResult:
        return PatchToolResult(content="patched")

    @registry.observe(AfterTool)
    async def observe(event: AfterTool) -> None:
        seen.append(event.result.content)

    event = AfterTool(run_id="run", call=call, result=original)
    await registry.dispatch(event)

    assert original.content == "raw"
    assert seen == ["patched"]
    assert event.result.content == "patched"


@pytest.mark.asyncio
async def test_chained_transform_context_interceptors_compose() -> None:
    registry = HandlerRegistry()
    seen: list[str] = []

    @registry.intercept(BeforeModel, priority=10)
    async def append_first(event: BeforeModel) -> TransformContext:
        return TransformContext((*event.request.messages, Message(MessageRole.USER, "first")))

    @registry.intercept(BeforeModel, priority=0)
    async def append_second(event: BeforeModel) -> TransformContext:
        assert event.request.messages[-1].content == "first"
        return TransformContext((*event.request.messages, Message(MessageRole.USER, "second")))

    @registry.observe(BeforeModel)
    async def observe(event: BeforeModel) -> None:
        seen.extend(message.content for message in event.request.messages)

    event = BeforeModel(
        run_id="run",
        request=ModelRequest((Message(MessageRole.USER, "hello"),), ()),
    )
    await registry.dispatch(event)

    assert seen == ["hello", "first", "second"]


@pytest.mark.asyncio
async def test_chained_patch_tool_result_interceptors_compose() -> None:
    registry = HandlerRegistry()
    call = ToolCall("id", "echo", {})
    event = AfterTool(run_id="run", call=call, result=ToolResult("raw", is_error=True))

    @registry.intercept(AfterTool, priority=10)
    async def normalize_content(_event: AfterTool) -> PatchToolResult:
        return PatchToolResult(content="normalized")

    @registry.intercept(AfterTool, priority=0)
    async def clear_error(event: AfterTool) -> PatchToolResult:
        assert event.result.content == "normalized"
        return PatchToolResult(is_error=False)

    await registry.dispatch(event)

    assert event.result.content == "normalized"
    assert event.result.is_error is False


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

    await registry.dispatch(SampleEvent(value="value"))

    assert calls == ["wildcard", "typed"]


@pytest.mark.asyncio
async def test_intercept_outcome_on_unsupported_event_is_ignored(
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = HandlerRegistry()

    @registry.intercept(RunStarted)
    async def noop(_event: RunStarted) -> BlockTool:
        return BlockTool("ignored")

    with caplog.at_level("WARNING"):
        outcome = await registry.dispatch(
            RunStarted(run_id="run", request=RunRequest("hi", Path(".")))
        )

    assert outcome is None
    assert "outcome ignored" in caplog.text


@pytest.mark.asyncio
async def test_wrong_intercept_outcome_type_on_before_tool_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = HandlerRegistry()

    @registry.intercept(BeforeTool)
    async def wrong(_event: BeforeTool) -> TransformContext:
        return TransformContext(())

    with caplog.at_level("WARNING"):
        await registry.dispatch(BeforeTool(run_id="run", call=ToolCall("id", "echo", {})))

    assert "expected BlockTool" in caplog.text
