from __future__ import annotations

from pathlib import Path

from milky_frog.domain import (
    Message,
    MessageRole,
    ModelRequest,
    ToolCall,
)
from milky_frog.harness.tokens import ApproxCharCounter, BudgetConfig, TokenBudget


class FakeTokenCounter:
    """Fake token counter for testing."""

    def __init__(self, model: str = "test") -> None:
        self._model = model

    def count_text(self, text: str) -> int:
        return len(text) // 4

    def count_messages(self, messages: list[dict[str, str]] | tuple[dict[str, str], ...]) -> int:
        total = len(messages) * 4
        for msg in messages:
            for value in msg.values():
                total += len(value) // 4
        return total

    def count_tool_schemas(self, schemas: tuple[dict, ...]) -> int:
        return len(schemas) * 100


class FixedCostCounter:
    """Counts a flat cost per message so budgets are predictable in tests."""

    def __init__(self, per_message: int = 10) -> None:
        self._per_message = per_message

    def count_text(self, text: str) -> int:
        return len(text) // 4

    def count_messages(self, messages: list[dict[str, str]] | tuple[dict[str, str], ...]) -> int:
        return len(messages) * self._per_message

    def count_tool_schemas(self, schemas: tuple[dict, ...]) -> int:
        return 0


def _budget(counter: object, input_budget: int) -> TokenBudget:
    budget = TokenBudget()
    budget._counter = counter  # type: ignore[assignment]
    budget._input_budget = input_budget
    budget._config = BudgetConfig(
        context_window=input_budget + 100, output_reserve=50, safety_margin=50
    )
    return budget


def test_budget_no_trim_when_under_budget() -> None:
    """Token budget should not trim when request is under budget."""
    budget = TokenBudget()
    budget._counter = FakeTokenCounter()
    budget._input_budget = 1000
    budget._config = BudgetConfig(context_window=1100, output_reserve=50, safety_margin=50)

    messages = (
        Message(MessageRole.SYSTEM, "You are helpful"),
        Message(MessageRole.USER, "Hello"),
    )
    request = ModelRequest(messages, ())

    assert budget.trim(request) == request


def test_budget_trims_when_over_budget() -> None:
    """Token budget should trim when request exceeds budget."""
    budget = TokenBudget()
    budget._counter = FakeTokenCounter()
    budget._input_budget = 100
    budget._config = BudgetConfig(context_window=150, output_reserve=30, safety_margin=20)

    messages = (
        Message(MessageRole.SYSTEM, "You are a helpful assistant" * 10),
        Message(MessageRole.USER, "Hello world" * 10),
        Message(MessageRole.ASSISTANT, "Hi there" * 10),
        Message(MessageRole.USER, "How are you?"),
    )
    request = ModelRequest(messages, ())

    trimmed = budget.trim(request)
    assert trimmed != request
    assert len(trimmed.messages) <= len(request.messages)


def test_budget_preserves_system_message() -> None:
    """Token budget should preserve system message even when trimming."""
    budget = TokenBudget()
    budget._counter = FakeTokenCounter()
    budget._input_budget = 80
    budget._config = BudgetConfig(context_window=130, output_reserve=30, safety_margin=20)

    messages = (
        Message(MessageRole.SYSTEM, "You are helpful"),
        Message(MessageRole.USER, "Hello" * 30),
        Message(MessageRole.ASSISTANT, "Hi" * 30),
    )
    request = ModelRequest(messages, ())

    trimmed = budget.trim(request)
    if trimmed != request:
        assert trimmed.messages[0].role == MessageRole.SYSTEM
        assert trimmed.messages[0].content == "You are helpful"


def test_budget_with_tools() -> None:
    """Token budget should account for tool schemas in token count."""
    budget = TokenBudget()
    budget._counter = FakeTokenCounter()
    budget._input_budget = 150
    budget._config = BudgetConfig(context_window=250, output_reserve=50, safety_margin=50)

    messages = (Message(MessageRole.USER, "Hello"),)
    tools = (
        {"name": "tool1", "description": "A tool"},
        {"name": "tool2", "description": "Another tool"},
    )
    request = ModelRequest(messages, tools)

    trimmed = budget.trim(request)
    if trimmed != request:
        assert trimmed.tools == tools


def test_approx_counter_counts_messages_and_tools() -> None:
    """ApproxCharCounter returns positive counts for messages and tool schemas."""
    counter = ApproxCharCounter()
    messages = [
        {"role": "user", "content": "Hello world"},
        {"role": "assistant", "content": "Hi there"},
    ]
    assert counter.count_messages(messages) > 0
    schemas = (
        {"name": "tool1", "description": "Tool description"},
        {"name": "tool2", "description": "Another tool"},
    )
    assert counter.count_tool_schemas(schemas) > 0


def test_trim_preserves_chronological_order_and_keeps_recent() -> None:
    """Trimming keeps a contiguous recent tail, dropping the oldest first."""
    budget = _budget(FixedCostCounter(per_message=10), input_budget=30)
    messages = (
        Message(MessageRole.SYSTEM, "sys"),
        Message(MessageRole.USER, "oldest"),
        Message(MessageRole.ASSISTANT, "middle"),
        Message(MessageRole.USER, "most recent question"),
    )
    request = ModelRequest(messages, ())

    trimmed = budget.trim(request)

    assert trimmed != request
    kept = trimmed.messages
    assert kept == (messages[0], messages[2], messages[3])


def test_trim_never_orphans_a_tool_result() -> None:
    """A tool result whose assistant turn is trimmed must not lead the request."""
    budget = _budget(FixedCostCounter(per_message=10), input_budget=30)
    messages = (
        Message(MessageRole.SYSTEM, "sys"),
        Message(MessageRole.ASSISTANT, "calls tool", tool_calls=(ToolCall("c1", "bash", {}),)),
        Message(MessageRole.TOOL, "tool output", tool_call_id="c1"),
        Message(MessageRole.USER, "next"),
    )
    request = ModelRequest(messages, ())

    trimmed = budget.trim(request)

    assert trimmed != request
    kept = trimmed.messages
    assert all(m.role != MessageRole.TOOL for m in kept)
    assert kept == (messages[0], messages[3])


def test_trim_keeps_assistant_with_its_tool_result() -> None:
    """When the assistant turn fits, its following tool result is kept with it."""
    budget = _budget(FixedCostCounter(per_message=10), input_budget=30)
    messages = (
        Message(MessageRole.SYSTEM, "sys"),
        Message(MessageRole.USER, "oldest"),
        Message(MessageRole.ASSISTANT, "calls tool", tool_calls=(ToolCall("c1", "bash", {}),)),
        Message(MessageRole.TOOL, "tool output", tool_call_id="c1"),
    )
    request = ModelRequest(messages, ())

    trimmed = budget.trim(request)

    assert trimmed != request
    kept = trimmed.messages
    assert kept == (messages[0], messages[2], messages[3])


def test_no_trim_when_system_and_tools_exceed_budget() -> None:
    """If the system prompt and tools alone blow the budget, send unmodified."""
    budget = _budget(FakeTokenCounter(), input_budget=50)
    messages = (
        Message(MessageRole.SYSTEM, "system prompt"),
        Message(MessageRole.USER, "hi"),
    )
    request = ModelRequest(messages, ({"name": "a"}, {"name": "b"}))

    assert budget.trim(request) == request


def test_count_includes_assistant_tool_call_arguments() -> None:
    """Assistant tool-call arguments are part of the request and must be counted."""
    budget = _budget(FakeTokenCounter(), input_budget=1000)
    plain = Message(MessageRole.ASSISTANT, "ok")
    with_call = Message(
        MessageRole.ASSISTANT,
        "ok",
        tool_calls=(ToolCall("c1", "bash", {"command": "echo hello world"}),),
    )

    plain_tokens = budget._count_request_tokens(ModelRequest((plain,), ()))
    call_tokens = budget._count_request_tokens(ModelRequest((with_call,), ()))

    assert call_tokens > plain_tokens


def test_budget_initialized_from_workspace(tmp_path: Path) -> None:
    """Workspace config seeds the input budget on init."""
    budget = TokenBudget()
    assert budget._counter is None

    budget.init_for_workspace(tmp_path)

    assert budget._counter is not None
    assert budget._config is not None
    assert budget._input_budget == 88000


def test_approx_char_counter_counts() -> None:
    """ApproxCharCounter produces positive counts without tiktoken."""
    counter = ApproxCharCounter()
    assert counter.count_text("hello world this is text") > 0
    assert counter.count_text("") == 0
    assert counter.count_messages([{"role": "user", "content": "hello there"}]) > 0
    assert counter.count_tool_schemas(({"name": "tool", "description": "does things"},)) > 0
