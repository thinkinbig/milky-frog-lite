from __future__ import annotations

from dataclasses import dataclass

from pydantic import JsonValue

# ── Slash commands ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SlashCommand:
    """A slash command surfaced in help and command completion."""

    name: str
    description: str
    usage: str = ""


COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand("/help", "Show available commands"),
    SlashCommand("/clear", "Clear the conversation and start fresh"),
    SlashCommand("/resume", "Attach to or continue a Run", usage="/resume RUN_ID [prompt]"),
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
