from __future__ import annotations

from milky_frog.domain import RunUsage


def _humanize(count: int) -> str:
    """Compact a token count: 980 -> "980", 1536 -> "1.5k", 23000 -> "23k"."""
    if count < 1000:
        return str(count)
    thousands = count / 1000
    if thousands < 10:
        return f"{thousands:.1f}k"
    return f"{round(thousands)}k"


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
