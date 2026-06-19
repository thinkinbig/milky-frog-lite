from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from pydantic import JsonValue


class RunStatus(StrEnum):
    RUNNING = "running"
    WAITING_FOR_INPUT = "waiting_for_input"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    PAUSED_LIMIT = "paused_limit"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class Message:
    role: MessageRole
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class ModelRequest:
    messages: tuple[Message, ...]
    tools: tuple[dict[str, JsonValue], ...]


@dataclass(frozen=True, slots=True)
class ModelResponse:
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RunRequest:
    prompt: str
    workspace: Path
    max_model_calls: int = 30


@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: str
    status: RunStatus
    final_message: str
    model_calls: int
