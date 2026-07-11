from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from milky_frog.domain.tools import ToolCall


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class Message:
    role: MessageRole
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    reasoning: str = ""
    """Provider reasoning required to replay this assistant Tool call."""
