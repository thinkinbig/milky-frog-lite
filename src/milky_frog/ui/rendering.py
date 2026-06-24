from __future__ import annotations

import difflib
import shutil
from dataclasses import dataclass
from typing import Literal

from pydantic import JsonValue
from rich.text import Text

DiffKind = Literal["add", "remove", "context"]

# ── Slash commands ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SlashCommand:
    """A slash command surfaced in help and command completion."""

    name: str
    description: str
    usage: str = ""


COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand("/help", "Show available commands"),
    SlashCommand("/runs", "List recent Runs"),
    SlashCommand("/clear", "Clear the conversation and start fresh"),
    SlashCommand("/resume", "Attach to or continue a Run", usage="/resume [RUN_ID] [prompt]"),
    SlashCommand("/exit", "Leave Milky Frog"),
)


def matching_commands(prefix: str) -> tuple[SlashCommand, ...]:
    """Return commands whose name starts with ``prefix`` (case-insensitive)."""
    folded = prefix.casefold()
    return tuple(command for command in COMMANDS if command.name.startswith(folded))


def complete_command(prefix: str) -> str | None:
    """Return the single command name completing ``prefix``, or ``None``.

    Completes when exactly one command matches, or when one match equals the
    longest common prefix of several (so ``/`` alone stays ambiguous).
    """
    matches = matching_commands(prefix)
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0].name
    return None


# ── Tool call / result formatting ──────────────────────────────────────

# Argument keys that carry the "primary" subject of a tool call, in priority
# order. The first one present is shown bare (e.g. ``Read(src/app.py)``).
_PRIMARY_KEYS = (
    "command",
    "cmd",
    "pattern",
    "query",
    "url",
    "path",
    "file_path",
    "file",
)

_MAX_ARG_LEN = 60


def _stringify(value: JsonValue) -> str:
    if isinstance(value, str):
        return value
    return str(value)


def _truncate(text: str, limit: int = _MAX_ARG_LEN) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1] + "…"


def format_tool_call(name: str, arguments: dict[str, JsonValue]) -> str:
    """Render a one-line ``Name(subject)`` signature for a tool call.

    Prefers a recognised primary argument; otherwise shows the first one or two
    ``key=value`` pairs. Long values are collapsed and truncated.
    """
    title = name[:1].upper() + name[1:] if name else name
    if not arguments:
        return f"{title}()"

    for key in _PRIMARY_KEYS:
        if key in arguments:
            return f"{title}({_truncate(_stringify(arguments[key]))})"

    pairs = [f"{key}={_truncate(_stringify(value), 24)}" for key, value in arguments.items()]
    shown = ", ".join(pairs[:2])
    if len(pairs) > 2:
        shown = f"{shown}, …"
    return f"{title}({shown})"


# Maximum lines of tool output to show inline.  Beyond this the first N lines
# are shown with a "+X more" suffix.
MAX_RESULT_LINES = 30


def summarize_tool_result(content: str, *, is_error: bool) -> str:
    """Render a compact ``⎿`` summary line for a tool result."""
    stripped = content.strip()
    if not stripped:
        return "(no output)" if not is_error else "(failed)"

    lines = stripped.splitlines()
    first = _truncate(lines[0], 80)
    if len(lines) > 1:
        return f"{first} (+{len(lines) - 1} more line{'s' if len(lines) - 1 != 1 else ''})"
    return first


def bash_output_renderable(content: str, *, is_error: bool) -> Text | None:
    """Render bash command output as a full inline block, or ``None`` if empty.

    Shared by git, grep, and generic bash handlers; each can wrap or replace
    this for command-specific formatting in the future.
    """
    stripped = content.strip()
    if not stripped:
        return None

    lines = stripped.splitlines()
    terminal_width = shutil.get_terminal_size().columns - 6  # indent
    max_width = min(terminal_width, 120)

    if len(lines) <= MAX_RESULT_LINES:
        body = "\n".join(
            line[:max_width] + ("…" if len(line) > max_width else "") for line in lines
        )
    else:
        shown = "\n".join(
            line[:max_width] + ("…" if len(line) > max_width else "")
            for line in lines[:MAX_RESULT_LINES]
        )
        extra = len(lines) - MAX_RESULT_LINES
        body = f"{shown}\n({extra} more line{'s' if extra != 1 else ''})"

    if is_error:
        return Text(body, style="red")
    return Text.from_ansi(body)


def tool_result_renderable(tool_name: str, content: str, *, is_error: bool) -> Text | None:
    """Render a non-bash tool's result as a full inline block, or ``None`` for summary.

    Bash results are routed through ``BashRenderHandler`` and never reach here.
    This hook remains for future non-bash tools that need inline rendering.
    """
    return None


# ── File-change diffs ──────────────────────────────────────────────────


def build_diff_lines(old: str, new: str) -> list[tuple[DiffKind, str]]:
    """A unified diff of ``old`` vs ``new`` as ``(kind, text)`` rows.

    ``kind`` is ``"add"`` / ``"remove"`` / ``"context"``. Hunk and file headers
    are dropped — for a replaced snippet the line numbers are not meaningful.
    """
    old_lines = old.splitlines() or [""]
    new_lines = new.splitlines() or [""]
    rows: list[tuple[DiffKind, str]] = []
    for line in difflib.unified_diff(old_lines, new_lines, lineterm="", n=2):
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith("+"):
            rows.append(("add", line[1:]))
        elif line.startswith("-"):
            rows.append(("remove", line[1:]))
        else:
            rows.append(("context", line[1:] if line.startswith(" ") else line))
    return rows


def file_change_diff(
    name: str, arguments: dict[str, JsonValue]
) -> list[tuple[DiffKind, str]] | None:
    """Derive a colored diff from a file-editing tool's call arguments, or ``None``.

    ``edit_file`` carries ``old``/``new`` and renders as a unified diff.
    ``write_file`` carries ``content`` and renders as all-additions (empty → content).
    Other tools return ``None`` (no inline diff preview).
    """
    if name == "edit_file" and "old" in arguments and "new" in arguments:
        return build_diff_lines(_stringify(arguments["old"]), _stringify(arguments["new"]))
    if name == "write_file" and "content" in arguments:
        return [("add", line) for line in _stringify(arguments["content"]).splitlines()] or None
    return None
