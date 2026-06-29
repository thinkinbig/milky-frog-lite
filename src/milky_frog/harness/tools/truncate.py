from __future__ import annotations

from pathlib import Path

from milky_frog.harness.tools.spill import spill_full_output
from milky_frog.tokens import ApproxCharCounter

_COUNTER = ApproxCharCounter()


def truncate_tool_output(
    text: str,
    max_chars: int = 30000,
    *,
    workspace: Path | None = None,
    label: str = "output",
) -> str:
    """Truncate tool output with a head-tail strategy.

    When *workspace* is given, the full text is spilled to a file under the
    Workspace and its path is named in the notice, so the model can retrieve the
    omitted middle with ``read_file`` instead of re-running the tool.
    """
    if len(text) <= max_chars:
        return text

    head_chars = int(max_chars * 0.2)
    tail_chars = max_chars - head_chars

    head_text = text[:head_chars]
    tail_text = text[-tail_chars:]

    tokens = _COUNTER.count_text(text)
    omitted = len(text) - max_chars

    saved_path = spill_full_output(workspace, label, text) if workspace is not None else None
    recovery = (
        f" Full text saved to {saved_path}; read it with read_file (offset/limit)."
        if saved_path is not None
        else ""
    )

    notice = (
        f"\n\n... (Truncated {omitted} characters. "
        f"Full output was {len(text)} chars, ~{tokens} tokens.{recovery}) ...\n\n"
    )

    return head_text + notice + tail_text
