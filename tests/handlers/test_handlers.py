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


# ── PolicyHandler tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_policy_handler_no_policy_returns_none() -> None:
    from milky_frog.handlers.policy import PolicyHandler

    registry = EventHub()
    # No policy set on context → handler returns None
    handler = PolicyHandler()
    handler.register(registry)

    results = await registry.broadcast(
        RunBeforeTool(run_id="run", call=ToolCall("id", "echo", {})),
    )
    assert results == []


@pytest.mark.asyncio
async def test_policy_handler_denies_tool() -> None:
    from milky_frog.handlers.context import BlockResult, HandlerContext
    from milky_frog.handlers.policy import PolicyHandler
    from milky_frog.harness.tools.registry import ToolRegistry
    from milky_frog.harness.tools.tool_policy import SessionToolPolicy

    policy = SessionToolPolicy(ToolRegistry())
    policy.deny("echo")

    registry = EventHub()
    registry.set_context(HandlerContext(policy=policy))
    handler = PolicyHandler()
    handler.register(registry)

    results = await registry.broadcast(
        RunBeforeTool(run_id="run", call=ToolCall("id", "echo", {})),
    )
    assert len(results) == 1
    assert isinstance(results[0], BlockResult)
    assert "denied" in results[0].reason


@pytest.mark.asyncio
async def test_policy_handler_approval_needed() -> None:
    from milky_frog.handlers.context import ApprovalResult, HandlerContext
    from milky_frog.handlers.policy import PolicyHandler
    from milky_frog.harness.tools.registry import ToolRegistry
    from milky_frog.harness.tools.tool_policy import SessionToolPolicy

    # With an empty registry, any call is unknown → NEEDS_APPROVAL
    policy = SessionToolPolicy(ToolRegistry())

    registry = EventHub()
    registry.set_context(HandlerContext(policy=policy))
    handler = PolicyHandler()
    handler.register(registry)

    results = await registry.broadcast(
        RunBeforeTool(run_id="run", call=ToolCall("id", "unknown_tool", {})),
    )
    assert len(results) == 1
    assert isinstance(results[0], ApprovalResult)


@pytest.mark.asyncio
async def test_policy_handler_allows_tool_and_returns_none() -> None:
    from milky_frog.handlers.context import HandlerContext
    from milky_frog.handlers.policy import PolicyHandler
    from milky_frog.harness.tools.registry import ToolRegistry
    from milky_frog.harness.tools.tool_policy import SessionToolPolicy

    class SafeTool:
        name = "safe"
        description = "safe tool"
        requires_approval = False

        async def execute(self, context, input): ...

    policy = SessionToolPolicy(ToolRegistry((SafeTool(),)))

    registry = EventHub()
    registry.set_context(HandlerContext(policy=policy))
    handler = PolicyHandler()
    handler.register(registry)

    results = await registry.broadcast(
        RunBeforeTool(run_id="run", call=ToolCall("id", "safe", {})),
    )
    assert results == []  # ALLOW returns None, no HandlerResult
