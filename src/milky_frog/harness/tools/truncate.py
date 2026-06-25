from __future__ import annotations

from milky_frog.harness.tokens import ApproxCharCounter

_COUNTER = ApproxCharCounter()


def truncate_tool_output(text: str, max_chars: int = 30000) -> str:
    """Truncate tool output with head-tail strategy."""
    if len(text) <= max_chars:
        return text

    head_chars = int(max_chars * 0.2)
    tail_chars = max_chars - head_chars

    head_text = text[:head_chars]
    tail_text = text[-tail_chars:]

    tokens = _COUNTER.count_text(text)
    omitted = len(text) - max_chars

    notice = (
        f"\n\n... (Truncated {omitted} characters. "
        f"Full output was {len(text)} chars, ~{tokens} tokens.) ...\n\n"
    )

    return head_text + notice + tail_text
