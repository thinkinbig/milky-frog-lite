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
class FollowUpCall:
    """A Tool's request to synthesize a follow-up ToolCall that must be approved.

    Lets a Tool's outcome deterministically pause the Run for a human decision
    (e.g. subagent leaving an unmerged worktree) without depending on the model
    choosing to raise it in its next turn — the loop turns this into a normal
    ``NEEDS_APPROVAL`` tool call (see ``AgentLoop.advance``).
    """

    tool_name: str
    arguments: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ToolResult:
    content: str
    is_error: bool = False
    display_content: str | None = None
    follow_up: FollowUpCall | None = None
