from __future__ import annotations

from milky_frog.domain import RunUsage, TokenUsage
from milky_frog.ui.usage import format_run_usage


def test_token_usage_total_and_recorded() -> None:
    empty = TokenUsage()
    assert empty.total_tokens == 0
    assert not empty.recorded

    usage = TokenUsage(input_tokens=10, output_tokens=5)
    assert usage.total_tokens == 15
    assert usage.recorded


def test_token_usage_add_sums_every_field() -> None:
    a = TokenUsage(input_tokens=10, output_tokens=5, cached_tokens=4, reasoning_tokens=2)
    b = TokenUsage(input_tokens=3, output_tokens=7, cached_tokens=1, reasoning_tokens=6)

    assert a + b == TokenUsage(
        input_tokens=13, output_tokens=12, cached_tokens=5, reasoning_tokens=8
    )


def test_run_usage_accumulates_billed_and_tracks_context() -> None:
    usage = RunUsage()
    usage = usage.record(TokenUsage(input_tokens=100, output_tokens=20))
    usage = usage.record(TokenUsage(input_tokens=140, output_tokens=30))

    # Cumulative is the billed sum across calls; context is the latest call's input.
    assert usage.cumulative == TokenUsage(input_tokens=240, output_tokens=50)
    assert usage.context_tokens == 140
    assert usage.recorded


def test_run_usage_keeps_last_context_when_a_call_reports_nothing() -> None:
    usage = RunUsage().record(TokenUsage(input_tokens=100, output_tokens=20))
    usage = usage.record(TokenUsage())  # provider omitted usage for this call

    assert usage.context_tokens == 100
    assert usage.cumulative == TokenUsage(input_tokens=100, output_tokens=20)


def test_format_run_usage_is_silent_without_recorded_usage() -> None:
    assert format_run_usage(RunUsage()) is None


def test_format_run_usage_humanizes_and_includes_subtotals() -> None:
    usage = RunUsage(
        cumulative=TokenUsage(
            input_tokens=1536, output_tokens=340, cached_tokens=512, reasoning_tokens=120
        ),
        context_tokens=1536,
    )

    summary = format_run_usage(usage)

    assert summary == "↑ 1.5k in · ↓ 340 out · Σ 1.9k tokens · 512 cached · 120 reasoning"


def test_format_run_usage_omits_zero_subtotals() -> None:
    usage = RunUsage(cumulative=TokenUsage(input_tokens=200, output_tokens=50))

    summary = format_run_usage(usage)

    assert summary == "↑ 200 in · ↓ 50 out · Σ 250 tokens"
