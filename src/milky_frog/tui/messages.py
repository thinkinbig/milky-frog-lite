from __future__ import annotations

from pydantic import JsonValue
from textual.message import Message

from milky_frog.domain import RunResult, RunStatus, RunUsage
from milky_frog.events.events import NoticeLevel


class AddThinking(Message):
    """Append a reasoning block to the conversation."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class AddText(Message):
    """Append assistant text to the conversation."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class ToolCallMsg(Message):
    """Mark a Tool call start, with its arguments for rich rendering."""

    def __init__(self, name: str, arguments: dict[str, JsonValue]) -> None:
        super().__init__()
        self.name = name
        self.arguments = arguments


class ToolResultMsg(Message):
    """Mark a Tool call result, with its content for a summary line."""

    def __init__(self, name: str, *, content: str, is_error: bool) -> None:
        super().__init__()
        self.name = name
        self.content = content
        self.is_error = is_error


class UpdateUsage(Message):
    """Update the running token usage display."""

    def __init__(self, usage: RunUsage) -> None:
        super().__init__()
        self.usage = usage


class RunFinished(Message):
    """A Run completed, paused, cancelled, or failed."""

    def __init__(
        self,
        result: RunResult,
        *,
        status: RunStatus,
        message: str,
    ) -> None:
        super().__init__()
        self.result = result
        self.status = status
        self.message = message


class ApprovalRequired(Message):
    """A Run paused waiting for the user to approve a pending tool call."""

    def __init__(self, run_id: str, reason: str, tool_name: str = "") -> None:
        super().__init__()
        self.run_id = run_id
        self.reason = reason
        self.tool_name = tool_name


class ApprovalOptionSelected(Message):
    """User picked an option from the inline approval menu."""

    def __init__(self, action: str) -> None:
        super().__init__()
        self.action = action


class RunOptionSelected(Message):
    """User selected a Run from the inline resume picker or dismissed it."""

    def __init__(self, run_id: str | None) -> None:
        super().__init__()
        self.run_id = run_id


class SkillOptionSelected(Message):
    """User confirmed a selection from the inline skill picker."""

    def __init__(self, selected: frozenset[str]) -> None:
        super().__init__()
        self.selected = selected  # empty set = deactivate all


class McpOptionSelected(Message):
    """User confirmed a selection from the inline MCP server picker."""

    def __init__(self, enabled: frozenset[str]) -> None:
        super().__init__()
        self.enabled = enabled  # names of servers the user wants enabled


class McpReloadRequested(Message):
    """Config was written; trigger an async MCP reconnect worker."""


class RunError(Message):
    """An unexpected error occurred during the Run."""

    def __init__(self, error: str) -> None:
        super().__init__()
        self.error = error


class RunNoticeMsg(Message):
    """Ephemeral user-facing message while a Run is in progress."""

    def __init__(self, message: str, *, level: NoticeLevel = "info") -> None:
        super().__init__()
        self.message = message
        self.level = level


class GitOutputMsg(Message):
    """git command output, routed for ANSI-color rendering."""

    def __init__(self, command: str, *, content: str, is_error: bool) -> None:
        super().__init__()
        self.command = command
        self.content = content
        self.is_error = is_error


class GrepOutputMsg(Message):
    """grep/rg output, routed for match-line rendering."""

    def __init__(self, command: str, *, content: str, is_error: bool) -> None:
        super().__init__()
        self.command = command
        self.content = content
        self.is_error = is_error


class BashOutputMsg(Message):
    """Generic bash output fallback."""

    def __init__(self, *, content: str, is_error: bool) -> None:
        super().__init__()
        self.content = content
        self.is_error = is_error


class CompactionMsg(Message):
    """Transcript compaction finished: ``messages_folded`` messages became a summary."""

    def __init__(self, messages_folded: int) -> None:
        super().__init__()
        self.messages_folded = messages_folded
