from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import JsonValue


class ToolDecision(StrEnum):
    """Permission decision for a tool call before execution."""

    ALLOW = "allow"
    DENY = "deny"
    NEEDS_APPROVAL = "needs_approval"


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ToolResult:
    content: str
    is_error: bool = False
    display_content: str | None = None
