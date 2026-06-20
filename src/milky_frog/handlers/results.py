from __future__ import annotations

from dataclasses import dataclass

from milky_frog.domain import Message


@dataclass(frozen=True, slots=True)
class BlockTool:
    """Skip Tool execution and return this message as an error Tool result."""

    reason: str


@dataclass(frozen=True, slots=True)
class TransformContext:
    """Replace the message list sent to the model on the next call."""

    messages: tuple[Message, ...]


@dataclass(frozen=True, slots=True)
class PatchToolResult:
    """Adjust a Tool result after execution; unset fields leave the prior value."""

    content: str | None = None
    is_error: bool | None = None
