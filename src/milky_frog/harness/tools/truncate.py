from __future__ import annotations

from pathlib import Path

from milky_frog.harness.tools.spill import spill_full_output
from milky_frog.tokens import TokenCounter


def truncate_tool_output(
    text: str,
    max_chars: int = 30000,
    *,
    workspace: Path | None = None,
    label: str = "output",
    counter: TokenCounter | None = None,
) -> str:
    """Truncate tool output with a head-tail strategy on line boundaries.

    Head and tail include only whole lines so ``read_file``'s line-based
    ``offset`` aligns with the omitted middle. Single-line or overlapping
    head/tail fall back to character truncation.

    When *workspace* is given, the full text is spilled to a file under the
    Workspace and its path is named in the notice, so the model can retrieve the
    omitted middle with ``read_file`` instead of re-running the tool.
    """
    if len(text) <= max_chars:
        return text

    lines = text.splitlines(keepends=True)
    first_omitted_line: int | None = None
    line_split = len(lines) > 1

    if line_split:
        head_budget = int(max_chars * 0.2)
        tail_budget = max_chars - head_budget
        head_end = 0
        head_len = 0
        for line in lines:
            if head_end > 0 and head_len + len(line) > head_budget:
                break
            head_len += len(line)
            head_end += 1
        tail_start = len(lines)
        tail_len = 0
        for index in range(len(lines) - 1, head_end - 1, -1):
            line = lines[index]
            if tail_len > 0 and tail_len + len(line) > tail_budget:
                break
            tail_len += len(line)
            tail_start = index
        if tail_start < head_end:
            line_split = False
        else:
            head_text = "".join(lines[:head_end])
            tail_text = "".join(lines[tail_start:])
            if tail_start > head_end:
                first_omitted_line = head_end + 1

    if not line_split:
        head_chars = int(max_chars * 0.2)
        tail_chars = max_chars - head_chars
        head_text = text[:head_chars]
        tail_text = text[-tail_chars:]
        first_omitted_line = head_text.count("\n") + 1

    omitted = len(text) - len(head_text) - len(tail_text)
    size_note = f"Full output was {len(text)} chars"
    if counter is not None:
        size_note += f", ~{counter.count_text(text)} tokens"

    saved_path = spill_full_output(workspace, label, text) if workspace is not None else None
    recovery = (
        f" Full text saved to {saved_path}; read the omitted middle with read_file "
        f"offset={first_omitted_line}."
        if saved_path is not None and first_omitted_line is not None
        else (
            f" Full text saved to {saved_path}; read it with read_file (offset/limit)."
            if saved_path is not None
            else ""
        )
    )

    notice = f"\n\n... (Truncated {omitted} characters. {size_note}.{recovery}) ...\n\n"

    return head_text + notice + tail_text
