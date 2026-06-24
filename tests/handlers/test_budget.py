from __future__ import annotations

from pathlib import Path

import pytest

from milky_frog.domain import (
    Message,
    MessageRole,
    ModelRequest,
    ModelResponse,
    TokenUsage,
    ToolCall,
)
from milky_frog.handlers.budget import BudgetConfig, BudgetHandler, OnlineAffineCalibrator
from milky_frog.handlers.context import BudgetedRequest, HandlerContext
from milky_frog.harness.tokens import ApproxCharCounter


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


def _budget_handler(counter: object, input_budget: int) -> BudgetHandler:
    handler = BudgetHandler()
    handler._counter = counter  # type: ignore[assignment]
    handler._input_budget = input_budget
    handler._config = BudgetConfig(
        context_window=input_budget + 100, output_reserve=50, safety_margin=50
    )
    return handler


def _event(request: ModelRequest) -> object:
    return type("RunBeforeModel", (), {"request": request})()


@pytest.mark.asyncio
async def test_budget_handler_no_trim_when_under_budget() -> None:
    """Budget handler should not trim when request is under budget."""
    handler = BudgetHandler()
    handler._counter = FakeTokenCounter()
    handler._input_budget = 1000
    handler._config = BudgetConfig(context_window=1100, output_reserve=50, safety_margin=50)

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
    handler = BudgetHandler()
    handler._counter = FakeTokenCounter()
    handler._input_budget = 100
    handler._config = BudgetConfig(context_window=150, output_reserve=30, safety_margin=20)

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
    handler = BudgetHandler()
    handler._counter = FakeTokenCounter()
    handler._input_budget = 80
    handler._config = BudgetConfig(context_window=130, output_reserve=30, safety_margin=20)

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
    handler = BudgetHandler()
    handler._counter = FakeTokenCounter()
    handler._input_budget = 150
    handler._config = BudgetConfig(context_window=250, output_reserve=50, safety_margin=50)

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
async def test_approx_counter_counts_messages_and_tools() -> None:
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


@pytest.mark.asyncio
async def test_trim_preserves_chronological_order_and_keeps_recent() -> None:
    """Trimming keeps a contiguous recent tail, dropping the oldest first."""
    handler = _budget_handler(FixedCostCounter(per_message=10), input_budget=30)
    messages = (
        Message(MessageRole.SYSTEM, "sys"),
        Message(MessageRole.USER, "oldest"),
        Message(MessageRole.ASSISTANT, "middle"),
        Message(MessageRole.USER, "most recent question"),
    )
    request = ModelRequest(messages, ())

    result = await handler._on_run_before_model(_event(request), HandlerContext())

    assert isinstance(result, BudgetedRequest)
    kept = result.request.messages
    # System retained, oldest user dropped, recent tail kept in order.
    assert kept == (messages[0], messages[2], messages[3])


@pytest.mark.asyncio
async def test_trim_never_orphans_a_tool_result() -> None:
    """A tool result whose assistant turn is trimmed must not lead the request."""
    handler = _budget_handler(FixedCostCounter(per_message=10), input_budget=30)
    messages = (
        Message(MessageRole.SYSTEM, "sys"),
        Message(MessageRole.ASSISTANT, "calls tool", tool_calls=(ToolCall("c1", "bash", {}),)),
        Message(MessageRole.TOOL, "tool output", tool_call_id="c1"),
        Message(MessageRole.USER, "next"),
    )
    request = ModelRequest(messages, ())

    result = await handler._on_run_before_model(_event(request), HandlerContext())

    assert isinstance(result, BudgetedRequest)
    kept = result.request.messages
    # The assistant turn does not fit, so its orphaned tool result is dropped too.
    assert all(m.role != MessageRole.TOOL for m in kept)
    assert kept == (messages[0], messages[3])


@pytest.mark.asyncio
async def test_trim_keeps_assistant_with_its_tool_result() -> None:
    """When the assistant turn fits, its following tool result is kept with it."""
    handler = _budget_handler(FixedCostCounter(per_message=10), input_budget=30)
    messages = (
        Message(MessageRole.SYSTEM, "sys"),
        Message(MessageRole.USER, "oldest"),
        Message(MessageRole.ASSISTANT, "calls tool", tool_calls=(ToolCall("c1", "bash", {}),)),
        Message(MessageRole.TOOL, "tool output", tool_call_id="c1"),
    )
    request = ModelRequest(messages, ())

    result = await handler._on_run_before_model(_event(request), HandlerContext())

    assert isinstance(result, BudgetedRequest)
    kept = result.request.messages
    assert kept == (messages[0], messages[2], messages[3])


@pytest.mark.asyncio
async def test_no_trim_when_system_and_tools_exceed_budget() -> None:
    """If the system prompt and tools alone blow the budget, send unmodified."""
    handler = _budget_handler(FakeTokenCounter(), input_budget=50)
    messages = (
        Message(MessageRole.SYSTEM, "system prompt"),
        Message(MessageRole.USER, "hi"),
    )
    # Two tool schemas alone cost 200 in FakeTokenCounter, over the 50 budget.
    request = ModelRequest(messages, ({"name": "a"}, {"name": "b"}))

    result = await handler._on_run_before_model(_event(request), HandlerContext())

    assert result is None


@pytest.mark.asyncio
async def test_count_includes_assistant_tool_call_arguments() -> None:
    """Assistant tool-call arguments are part of the request and must be counted."""
    handler = _budget_handler(FakeTokenCounter(), input_budget=1000)
    plain = Message(MessageRole.ASSISTANT, "ok")
    with_call = Message(
        MessageRole.ASSISTANT,
        "ok",
        tool_calls=(ToolCall("c1", "bash", {"command": "echo hello world"}),),
    )

    plain_tokens = handler._count_request_tokens(ModelRequest((plain,), ()))
    call_tokens = handler._count_request_tokens(ModelRequest((with_call,), ()))

    assert call_tokens > plain_tokens


@pytest.mark.asyncio
async def test_budget_initialized_on_resume(tmp_path: Path) -> None:
    """Resumed Runs never see RunStarted; RunBeforeResume must initialize the budget."""
    handler = BudgetHandler()
    assert handler._counter is None

    event = type("RunBeforeResume", (), {"workspace": tmp_path})()
    await handler._on_run_before_resume(event, HandlerContext())

    assert handler._counter is not None
    assert handler._config is not None
    # tmp_path has no config.toml, so defaults apply: 128000 - 8000 - 1000.
    assert handler._input_budget == 119000


def test_approx_char_counter_counts() -> None:
    """ApproxCharCounter produces positive counts without tiktoken."""
    counter = ApproxCharCounter()
    assert counter.count_text("hello world this is text") > 0
    assert counter.count_text("") == 0
    assert counter.count_messages([{"role": "user", "content": "hello there"}]) > 0
    assert counter.count_tool_schemas(({"name": "tool", "description": "does things"},)) > 0


def _after_model_event(request: ModelRequest, input_tokens: int) -> object:
    usage = TokenUsage(input_tokens=input_tokens, output_tokens=5)
    response = ModelResponse(content="ok", usage=usage)
    return type("RunAfterModel", (), {"request": request, "response": response})()


@pytest.mark.asyncio
async def test_calibration_learns_from_reported_usage() -> None:
    """One measurement anchors the estimate to the provider's reported input."""
    handler = _budget_handler(FixedCostCounter(per_message=10), input_budget=1000)
    request = ModelRequest((Message(MessageRole.USER, "hi"),), ())  # raw = 10

    # Single point -> proportional fit: real/raw = 40/10 = 4x.
    await handler._on_run_after_model(
        _after_model_event(request, input_tokens=40), HandlerContext()
    )
    assert handler._count_request_tokens(request) == 40


@pytest.mark.asyncio
async def test_calibration_ignored_when_usage_not_reported() -> None:
    """A provider that omits usage (all zeros) leaves the estimate uncalibrated."""
    handler = _budget_handler(FixedCostCounter(per_message=10), input_budget=1000)
    request = ModelRequest((Message(MessageRole.USER, "hi"),), ())

    await handler._on_run_after_model(_after_model_event(request, input_tokens=0), HandlerContext())
    # No sample recorded -> raw estimate is returned unchanged.
    assert handler._count_request_tokens(request) == handler._raw_count(request)


@pytest.mark.asyncio
async def test_calibration_clamped_to_sane_range() -> None:
    """A wild single measurement cannot push the slope past the cap."""
    handler = _budget_handler(FixedCostCounter(per_message=10), input_budget=1000)
    request = ModelRequest((Message(MessageRole.USER, "hi"),), ())  # raw = 10

    # raw=10, reported=100000 -> 10000x, slope clamped to MAX (8) -> 80.
    await handler._on_run_after_model(
        _after_model_event(request, input_tokens=100000), HandlerContext()
    )
    assert handler._count_request_tokens(request) == BudgetHandler._MAX_CALIBRATION * 10


@pytest.mark.asyncio
async def test_calibration_affine_fit_separates_fixed_overhead() -> None:
    """Two points at different sizes recover a line (fixed overhead + slope)."""
    handler = _budget_handler(FixedCostCounter(per_message=10), input_budget=1000)
    msgs = tuple(Message(MessageRole.USER, f"m{i}") for i in range(3))
    small = ModelRequest(msgs[:1], ())  # raw = 10
    large = ModelRequest(msgs[:3], ())  # raw = 30

    # Points (10, 120) and (30, 160) lie on real = 100 + 2*raw.
    await handler._on_run_after_model(_after_model_event(small, input_tokens=120), HandlerContext())
    await handler._on_run_after_model(_after_model_event(large, input_tokens=160), HandlerContext())

    # A mid-size request (raw=20) should predict 100 + 2*20 = 140 on that line,
    # which the proportional single-factor model could never produce.
    mid = ModelRequest(msgs[:2], ())  # raw = 20
    assert handler._count_request_tokens(mid) == 140


@pytest.mark.asyncio
async def test_calibration_makes_trimming_react_to_real_tool_cost() -> None:
    """After calibration, a request our raw count thinks fits gets trimmed."""
    handler = _budget_handler(FixedCostCounter(per_message=10), input_budget=35)
    messages = (
        Message(MessageRole.SYSTEM, "sys"),
        Message(MessageRole.USER, "old"),
        Message(MessageRole.USER, "recent"),
    )
    request = ModelRequest(messages, ())

    # Raw estimate = 30 (<= 35), so without calibration nothing trims.
    assert await handler._on_run_before_model(_event(request), HandlerContext()) is None

    # Provider actually charged 60 for that request -> 2x. Now 30*2 = 60 > 35.
    await handler._on_run_after_model(
        _after_model_event(request, input_tokens=60), HandlerContext()
    )
    result = await handler._on_run_before_model(_event(request), HandlerContext())
    assert isinstance(result, BudgetedRequest)


@pytest.mark.asyncio
async def test_calibration_same_size_uses_mean_ratio() -> None:
    """Repeated measurements at the same raw size average into one ratio."""
    handler = _budget_handler(FixedCostCounter(per_message=10), input_budget=1000)
    request = ModelRequest((Message(MessageRole.USER, "hi"),), ())  # raw = 10

    await handler._on_run_after_model(
        _after_model_event(request, input_tokens=20), HandlerContext()
    )
    assert handler._count_request_tokens(request) == 20

    await handler._on_run_after_model(
        _after_model_event(request, input_tokens=40), HandlerContext()
    )
    # Colinear points -> sum(y)/sum(x) = 60/20 = 3x -> 30.
    assert handler._count_request_tokens(request) == 30


@pytest.mark.asyncio
async def test_calibration_outlier_at_same_size_averages_in() -> None:
    """An outlier at the same raw size moves the estimate to the mean ratio."""
    handler = _budget_handler(FixedCostCounter(per_message=10), input_budget=1000)
    request = ModelRequest((Message(MessageRole.USER, "hi"),), ())  # raw = 10

    await handler._on_run_after_model(
        _after_model_event(request, input_tokens=20), HandlerContext()
    )

    await handler._on_run_after_model(
        _after_model_event(request, input_tokens=80), HandlerContext()
    )
    # sum(y)/sum(x) = 100/20 = 5x -> 50.
    assert handler._count_request_tokens(request) == 50


# --- OnlineAffineCalibrator (direct unit tests) ---------------------------------


def test_calibrator_identity_before_any_fit() -> None:
    """An unfitted calibrator predicts the raw value unchanged (calibration = 1.0)."""
    cal = OnlineAffineCalibrator()
    assert not cal.fitted
    assert cal.coef_ == 1.0
    assert cal.intercept_ == 0.0
    assert cal.predict(42) == 42.0


def test_calibrator_ignores_non_positive_measurements() -> None:
    """Zero/negative raw or real carry no usage to anchor to and are dropped."""
    cal = OnlineAffineCalibrator()
    cal.partial_fit(0, 100)
    cal.partial_fit(10, 0)
    cal.partial_fit(-5, -5)
    assert not cal.fitted
    assert cal.predict(10) == 10.0


def test_calibrator_single_sample_is_proportional() -> None:
    """One point gives a proportional fit real/raw with no intercept."""
    cal = OnlineAffineCalibrator()
    cal.partial_fit(10, 40)  # 4x
    assert cal.fitted
    assert cal.coef_ == 4.0
    assert cal.intercept_ == 0.0
    assert cal.predict(10) == 40.0
    assert cal.predict(25) == 100.0


def test_calibrator_recovers_affine_line_from_two_sizes() -> None:
    """Two differently-sized points recover slope and fixed intercept."""
    cal = OnlineAffineCalibrator()
    # Points (10, 120) and (30, 160) lie on real = 100 + 2*raw.
    cal.partial_fit(10, 120)
    cal.partial_fit(30, 160)
    assert cal.coef_ == pytest.approx(2.0)
    assert cal.intercept_ == pytest.approx(100.0)
    assert cal.predict(20) == pytest.approx(140.0)


def test_calibrator_clamps_slope_to_max_coef() -> None:
    """A wild ratio is capped at max_coef instead of exploding."""
    cal = OnlineAffineCalibrator(max_coef=8.0)
    cal.partial_fit(10, 100000)  # 10000x
    assert cal.coef_ == 8.0
    assert cal.predict(10) == 80.0


def test_calibrator_floors_prediction_against_underestimate() -> None:
    """Predictions never fall below min_coef * raw (the overflow-safe floor)."""
    cal = OnlineAffineCalibrator(min_coef=0.25)
    cal.partial_fit(100, 10)  # 0.1x, below the floor
    assert cal.predict(100) == pytest.approx(25.0)  # 0.25 * 100, not 10


def test_calibrator_caps_intercept_at_max_intercept() -> None:
    """The fixed-overhead intercept cannot exceed max_intercept."""
    cal = OnlineAffineCalibrator(max_intercept=50.0)
    # A line with a large intercept: (10, 1000), (30, 1040) -> ~ 980 + 2*raw.
    cal.partial_fit(10, 1000)
    cal.partial_fit(30, 1040)
    assert cal.intercept_ == 50.0


def test_calibrator_same_size_averages_ratio() -> None:
    """Repeated measurements at the same raw size use the mean ratio."""
    cal = OnlineAffineCalibrator()
    cal.partial_fit(10, 20)
    assert cal.predict(10) == 20.0
    cal.partial_fit(10, 40)
    assert cal.predict(10) == pytest.approx(30.0)
