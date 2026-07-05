from __future__ import annotations

from milky_frog.domain import RunUsage

# Width of the context-window meter bar, in cells.
_METER_WIDTH = 8
_METER_FILLED = "█"
_METER_EMPTY = "░"


def _humanize(count: int) -> str:
    """Compact a token count: 980 -> "980", 1536 -> "1.5k", 23000 -> "23k"."""
    if count < 1000:
        return str(count)
    thousands = count / 1000
    if thousands < 10:
        return f"{thousands:.1f}k"
    return f"{round(thousands)}k"


def context_fraction(context_tokens: int, context_window: int) -> float | None:
    """How full the context window is (0.0 to 1.0), or ``None`` if unknown.

    ``None`` when nothing has been measured yet or the window is unset, so
    callers stay silent rather than show a meaningless ``0%``.
    """
    if context_tokens <= 0 or context_window <= 0:
        return None
    return min(1.0, context_tokens / context_window)


def format_context_meter(context_tokens: int, context_window: int) -> str | None:
    """Render the pi-style context gauge ``28k/128k ████░░░░ 22%``.

    ``context_tokens`` is the live conversation footprint (the most recent
    call's input), measured against the model's ``context_window``. Returns
    ``None`` when there is nothing meaningful to show — see :func:`context_fraction`.
    """
    fraction = context_fraction(context_tokens, context_window)
    if fraction is None:
        return None
    filled = round(fraction * _METER_WIDTH)
    bar = _METER_FILLED * filled + _METER_EMPTY * (_METER_WIDTH - filled)
    return f"{_humanize(context_tokens)}/{_humanize(context_window)} {bar} {round(fraction * 100)}%"


def format_run_usage(usage: RunUsage) -> str | None:
    """Render a one-line token summary, or ``None`` if the provider reported none.

    Shows the cumulative billed input/output (what the Run is charged for) and,
    when the provider distinguishes them, the prompt-cache and reasoning
    sub-totals. ``None`` lets callers stay silent rather than print a misleading
    zero for gateways that omit usage.
    """
    if not usage.recorded:
        return None
    cumulative = usage.cumulative
    parts = [
        f"↑ {_humanize(cumulative.input_tokens)} in",
        f"↓ {_humanize(cumulative.output_tokens)} out",
        f"Σ {_humanize(cumulative.total_tokens)} tokens",
    ]
    if cumulative.cached_tokens:
        parts.append(f"{_humanize(cumulative.cached_tokens)} cached")
    if cumulative.reasoning_tokens:
        parts.append(f"{_humanize(cumulative.reasoning_tokens)} reasoning")
    return " · ".join(parts)
