from dataclasses import dataclass

import pytest

from milky_frog.domain import ToolCall
from milky_frog.handlers.dispatcher import EventDispatcher
from milky_frog.handlers.events import BaseEvent, RunBeforeTool


@dataclass(frozen=True)
class SampleEvent(BaseEvent):
    value: str


@pytest.mark.asyncio
async def test_observe_handlers_run_by_priority_then_registration_order() -> None:
    registry = EventDispatcher()
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

    await registry.notify(SampleEvent(run_id="test", value="value"))

    assert calls == ["first:value", "second:value", "low:value"]


@pytest.mark.asyncio
async def test_on_registers_observe_handlers() -> None:
    registry = EventDispatcher()
    calls: list[str] = []

    @registry.on(SampleEvent)
    async def record(event: SampleEvent, _ctx=None) -> None:
        calls.append(event.value)

    await registry.notify(SampleEvent(run_id="test", value="legacy"))

    assert calls == ["legacy"]


@pytest.mark.asyncio
async def test_subscribe_receives_every_notified_signal() -> None:
    registry = EventDispatcher()
    seen: list[str] = []

    @registry.subscribe
    async def record(event: BaseEvent, _ctx=None) -> None:
        seen.append(type(event).__name__)

    await registry.notify(SampleEvent(run_id="test", value="one"))
    await registry.notify(RunBeforeTool(run_id="run", call=ToolCall("id", "echo", {})))

    assert seen == ["SampleEvent", "RunBeforeTool"]


@pytest.mark.asyncio
async def test_subscribe_runs_by_priority_with_typed_observe_handlers() -> None:
    registry = EventDispatcher()
    calls: list[str] = []

    async def wildcard_first(_event: BaseEvent, _ctx=None) -> None:
        calls.append("wildcard")

    registry.subscribe(wildcard_first, priority=10)

    @registry.observe(SampleEvent)
    async def typed(_event: SampleEvent, _ctx=None) -> None:
        calls.append("typed")

    await registry.notify(SampleEvent(run_id="test", value="value"))

    assert calls == ["wildcard", "typed"]


# ── PolicyHandler tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_policy_handler_no_policy_returns_none() -> None:
    from milky_frog.handlers.policy import PolicyHandler

    registry = EventDispatcher()
    # No policy set on context → handler returns None
    handler = PolicyHandler()
    handler.register(registry)

    results = await registry.notify(
        RunBeforeTool(run_id="run", call=ToolCall("id", "echo", {})),
    )
    assert results == []


@pytest.mark.asyncio
async def test_policy_handler_denies_tool() -> None:
    from milky_frog.handlers.context import BlockResult, HandlerContext
    from milky_frog.handlers.policy import PolicyHandler
    from milky_frog.harness.tools.tool_policy import SessionToolPolicy

    policy = SessionToolPolicy(tools=())
    policy.deny("echo")

    registry = EventDispatcher()
    registry.set_context(HandlerContext(policy=policy))
    handler = PolicyHandler()
    handler.register(registry)

    results = await registry.notify(
        RunBeforeTool(run_id="run", call=ToolCall("id", "echo", {})),
    )
    assert len(results) == 1
    assert isinstance(results[0], BlockResult)
    assert "denied" in results[0].reason


@pytest.mark.asyncio
async def test_policy_handler_approval_needed() -> None:
    from milky_frog.handlers.context import ApprovalResult, HandlerContext
    from milky_frog.handlers.policy import PolicyHandler
    from milky_frog.harness.tools.tool_policy import SessionToolPolicy

    # With empty tools tuple, any call is unknown → NEEDS_APPROVAL
    policy = SessionToolPolicy(tools=())

    registry = EventDispatcher()
    registry.set_context(HandlerContext(policy=policy))
    handler = PolicyHandler()
    handler.register(registry)

    results = await registry.notify(
        RunBeforeTool(run_id="run", call=ToolCall("id", "unknown_tool", {})),
    )
    assert len(results) == 1
    assert isinstance(results[0], ApprovalResult)


@pytest.mark.asyncio
async def test_policy_handler_allows_tool_and_returns_none() -> None:
    from milky_frog.handlers.context import HandlerContext
    from milky_frog.handlers.policy import PolicyHandler
    from milky_frog.harness.tools.tool_policy import SessionToolPolicy

    class SafeTool:
        name = "safe"
        description = "safe tool"
        requires_approval = False

        async def execute(self, context, input): ...

    policy = SessionToolPolicy(tools=(SafeTool(),))

    registry = EventDispatcher()
    registry.set_context(HandlerContext(policy=policy))
    handler = PolicyHandler()
    handler.register(registry)

    results = await registry.notify(
        RunBeforeTool(run_id="run", call=ToolCall("id", "safe", {})),
    )
    assert results == []  # ALLOW returns None, no HandlerResult
