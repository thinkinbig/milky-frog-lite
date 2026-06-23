from __future__ import annotations

from pydantic import JsonValue
from textual.message import Message

from milky_frog.domain import RunResult, RunStatus, RunUsage
from milky_frog.handlers.events import NoticeLevel


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
