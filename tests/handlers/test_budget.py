from __future__ import annotations

import pytest

from milky_frog.domain import Message, MessageRole, ModelRequest
from milky_frog.handlers.budget import BudgetConfig, BudgetHandler
from milky_frog.handlers.context import BudgetedRequest, HandlerContext
from milky_frog.harness.tokens import TiktokenCounter


class FakeTokenCounter:
    """Fake token counter for testing."""

    def __init__(self, model: str = "test") -> None:
        self._model = model

    def count_text(self, text: str) -> int:
        return len(text) // 4

    def count_messages(
        self, messages: list[dict[str, str]] | tuple[dict[str, str], ...]
    ) -> int:
        total = len(messages) * 4
        for msg in messages:
            for value in msg.values():
                total += len(value) // 4
        return total

    def count_tool_schemas(self, schemas: tuple[dict, ...]) -> int:
        return len(schemas) * 100


@pytest.mark.asyncio
async def test_budget_handler_no_trim_when_under_budget() -> None:
    """Budget handler should not trim when request is under budget."""
    handler = BudgetHandler("gpt-4")
    handler._counter = FakeTokenCounter()
    handler._input_budget = 1000
    handler._config = BudgetConfig(
        context_window=1100, output_reserve=50, safety_margin=50
    )

    messages = (
        Message(MessageRole.SYSTEM, "You are helpful"),
        Message(MessageRole.USER, "Hello"),
    )
    request = ModelRequest(messages, ())
    event = type("RunBeforeModel", (), {"request": request})()

    result = await handler._on_run_before_model(event, HandlerContext())
    assert result is None


@pytest.mark.asyncio
async def test_budget_handler_trims_when_over_budget() -> None:
    """Budget handler should trim when request exceeds budget."""
    handler = BudgetHandler("gpt-4")
    handler._counter = FakeTokenCounter()
    handler._input_budget = 100
    handler._config = BudgetConfig(
        context_window=150, output_reserve=30, safety_margin=20
    )

    messages = (
        Message(MessageRole.SYSTEM, "You are a helpful assistant" * 10),
        Message(MessageRole.USER, "Hello world" * 10),
        Message(MessageRole.ASSISTANT, "Hi there" * 10),
        Message(MessageRole.USER, "How are you?"),
    )
    request = ModelRequest(messages, ())
    event = type("RunBeforeModel", (), {"request": request})()

    result = await handler._on_run_before_model(event, HandlerContext())
    assert result is not None
    assert isinstance(result, BudgetedRequest)
    assert result.request != request
    assert len(result.request.messages) <= len(request.messages)


@pytest.mark.asyncio
async def test_budget_handler_preserves_system_message() -> None:
    """Budget handler should preserve system message even when trimming."""
    handler = BudgetHandler("gpt-4")
    handler._counter = FakeTokenCounter()
    handler._input_budget = 80
    handler._config = BudgetConfig(
        context_window=130, output_reserve=30, safety_margin=20
    )

    messages = (
        Message(MessageRole.SYSTEM, "You are helpful"),
        Message(MessageRole.USER, "Hello" * 30),
        Message(MessageRole.ASSISTANT, "Hi" * 30),
    )
    request = ModelRequest(messages, ())
    event = type("RunBeforeModel", (), {"request": request})()

    result = await handler._on_run_before_model(event, HandlerContext())
    if result is not None:
        trimmed = result.request
        assert trimmed.messages[0].role == MessageRole.SYSTEM
        assert trimmed.messages[0].content == "You are helpful"


@pytest.mark.asyncio
async def test_budget_handler_with_tools() -> None:
    """Budget handler should account for tool schemas in token count."""
    handler = BudgetHandler("gpt-4")
    handler._counter = FakeTokenCounter()
    handler._input_budget = 150
    handler._config = BudgetConfig(
        context_window=250, output_reserve=50, safety_margin=50
    )

    messages = (Message(MessageRole.USER, "Hello"),)
    tools = (
        {"name": "tool1", "description": "A tool"},
        {"name": "tool2", "description": "Another tool"},
    )
    request = ModelRequest(messages, tools)
    event = type("RunBeforeModel", (), {"request": request})()

    result = await handler._on_run_before_model(event, HandlerContext())
    if result is not None:
        assert result.request.tools == tools


@pytest.mark.asyncio
async def test_token_counter_counts_text() -> None:
    """TiktokenCounter should count text tokens."""
    counter = TiktokenCounter("gpt-4o")
    count = counter.count_text("Hello world")
    assert count > 0
    assert isinstance(count, int)


@pytest.mark.asyncio
async def test_token_counter_counts_messages() -> None:
    """TiktokenCounter should count tokens in messages."""
    counter = TiktokenCounter("gpt-4o")
    messages = [
        {"role": "user", "content": "Hello world"},
        {"role": "assistant", "content": "Hi there"},
    ]
    count = counter.count_messages(messages)
    assert count > 0
    assert isinstance(count, int)


@pytest.mark.asyncio
async def test_token_counter_counts_tool_schemas() -> None:
    """TiktokenCounter should count tokens in tool schemas."""
    counter = TiktokenCounter("gpt-4o")
    schemas = (
        {"name": "tool1", "description": "Tool description"},
        {"name": "tool2", "description": "Another tool"},
    )
    count = counter.count_tool_schemas(schemas)
    assert count > 0
    assert isinstance(count, int)


def test_token_counter_encoding_selection() -> None:
    """TiktokenCounter should select correct encoding based on model."""
    counter_o1 = TiktokenCounter("o1")
    counter_gpt4 = TiktokenCounter("gpt-4")

    o1_tokens = counter_o1.count_text("test")
    gpt4_tokens = counter_gpt4.count_text("test")

    assert o1_tokens > 0
    assert gpt4_tokens > 0
