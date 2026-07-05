from __future__ import annotations

from pydantic import JsonValue
from rich.console import RenderableType
from rich.table import Table
from rich.text import Text

from milky_frog.domain import RunUsage
from milky_frog.tui.rendering import (
    DiffKind,
    bash_output_renderable,
    file_change_diff,
    format_tool_call,
    summarize_tool_result,
    tool_result_renderable,
)
from milky_frog.tui.usage import format_run_usage

# ── General helpers ────────────────────────────────────────────────────


def conversation_row(marker: Text, body: RenderableType) -> Table:
    """A hanging-indent grid: a fixed marker column beside a wrapping body.

    Rendered inside a ``Static``, the ratio body column re-wraps to the widget's
    width, so the conversation reflows when the terminal is resized.
    """
    row = Table.grid(padding=(0, 1))
    row.add_column(no_wrap=True, vertical="top")
    row.add_column(ratio=1)
    row.add_row(marker, body)
    return row


# ── Thinking (reasoning) ──────────────────────────────────────────────


def thinking_block(text: str, *, spinner: str) -> Text:
    """Render the reasoning block: braille spinner frame plus ``thinking``."""
    header = f"{spinner} thinking"
    if not text:
        return Text(header, style="dim italic")
    return Text.assemble((f"{header}\n", "dim italic"), (text, "dim italic"))


# ── Diff rendering ─────────────────────────────────────────────────────

_DIFF_ROW_STYLE: dict[DiffKind, str] = {
    "add": "#b9f6b0 on #0f2e17",
    "remove": "#f6b0b0 on #2e0f14",
    "context": "dim",
}
_DIFF_SIGN: dict[DiffKind, str] = {"add": "+ ", "remove": "- ", "context": "  "}
_MAX_DIFF_LINES = 40


def diff_renderable(rows: list[tuple[DiffKind, str]]) -> Table:
    """Render diff rows as full-width highlighted lines (green add, red remove, dim ctx).

    A one-column ``expand``ed grid: each row's style fills the whole line, so the
    highlight spans the terminal width and reflows on resize.
    """
    table = Table.grid(expand=True, padding=(0, 0, 0, 2))
    table.add_column()
    shown = rows[:_MAX_DIFF_LINES]
    for kind, line in shown:
        table.add_row(f"{_DIFF_SIGN[kind]}{line}", style=_DIFF_ROW_STYLE[kind])
    extra = len(rows) - len(shown)
    if extra:
        table.add_row(f"… {extra} more line{'s' if extra != 1 else ''}", style="bright_black")
    return table


# ── Tool call rendering ───────────────────────────────────────────────


def tool_call_widget(signature: str, *, spinner: str) -> Text:
    """The initial spinner widget for an in-flight tool call."""
    return Text.assemble((f"  {spinner} ", "bold yellow"), (signature, "cyan"))


def tool_call_completed(signature: str, *, is_error: bool) -> Text:
    """Final mark for a completed tool call."""
    mark, style = ("✗", "bold red") if is_error else ("⏺", "bold cyan")
    return Text.assemble((f"  {mark} ", style), (signature, "cyan"))


def tool_call_diff(name: str, arguments: dict[str, JsonValue]) -> list[tuple[DiffKind, str]] | None:
    """If the tool is a file edit, return diff rows; otherwise None."""
    return file_change_diff(name, arguments)


# ── Result rendering ──────────────────────────────────────────────────


def render_command_output(content: str, *, is_error: bool) -> RenderableType | None:
    """Full inline block for bash-family output; None if the output is empty."""
    return bash_output_renderable(content, is_error=is_error)


def command_summary(content: str, *, is_error: bool) -> Text:
    """Compact one-line summary for a tool result."""
    summary = summarize_tool_result(content, is_error=is_error)
    mark, style = ("✗", "red") if is_error else ("⎿", "bright_black")
    return Text.assemble((f"    {mark} ", style), (summary, "dim"))


# ── Formatting ─────────────────────────────────────────────────────────


def format_tool_signature(name: str, arguments: dict[str, JsonValue]) -> str:
    return format_tool_call(name, arguments)


def tool_result_block(name: str, content: str, *, is_error: bool) -> RenderableType | None:
    """Full inline block for a non-bash tool result; None when it fits a summary."""
    return tool_result_renderable(name, content, is_error=is_error)


# ── User message ──────────────────────────────────────────────────────


def user_row(text: str) -> Table:
    return conversation_row(Text("▸", style="bold cyan"), Text(text))


# ── Assistant footer ──────────────────────────────────────────────────


def assistant_footer(run_id: str, *, usage: RunUsage | None = None) -> Text:
    footer = f"  ⎿ run {run_id[:8]}"
    summary = format_run_usage(usage) if usage is not None else None
    if summary is not None:
        footer = f"{footer} · {summary}"
    return Text(footer, style="bright_black")
