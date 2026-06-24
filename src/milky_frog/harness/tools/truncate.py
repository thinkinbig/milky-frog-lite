from __future__ import annotations

import os
import tempfile

from milky_frog.harness.tokens import ApproxCharCounter

_COUNTER = ApproxCharCounter()


def truncate_tool_output(text: str, max_chars: int = 30000, tool_name: str = "tool") -> str:
    """Truncate tool output with head-tail strategy and spill full output to disk."""
    if len(text) <= max_chars:
        return text

    fd, path = tempfile.mkstemp(prefix=f"milky-frog-{tool_name}-", suffix=".log", dir="/tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)

    head_chars = int(max_chars * 0.2)
    tail_chars = max_chars - head_chars

    head_text = text[:head_chars]
    tail_text = text[-tail_chars:]

    tokens = _COUNTER.count_text(text)
    omitted = len(text) - max_chars

    notice = (
        f"\n\n... (Truncated {omitted} characters. "
        f"Full output ({len(text)} chars, ~{tokens} tokens) saved to {path}) ...\n\n"
    )

    return head_text + notice + tail_text
