from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import JsonValue

from milky_frog.domain.messages import Message
from milky_frog.domain.tools import ToolCall
from milky_frog.domain.usage import TokenUsage


@dataclass(frozen=True, slots=True)
class ModelRequest:
    messages: tuple[Message, ...]
    tools: tuple[dict[str, JsonValue], ...]
    run_id: str = ""


@dataclass(frozen=True, slots=True)
class ModelResponse:
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: TokenUsage = field(default_factory=TokenUsage)
    model: str = ""
    reasoning: str = ""


@dataclass(frozen=True, slots=True)
class TextDelta:
    """A streamed fragment of assistant text."""

    content: str


@dataclass(frozen=True, slots=True)
class ReasoningDelta:
    """A streamed fragment of a reasoning model's thinking.

    Surfaced for display and Checkpoint fidelity only; it is never fed back into
    the conversation, since reasoning providers reject their own reasoning on input.
    """

    content: str


@dataclass(frozen=True, slots=True)
class StreamDone:
    """The terminal chunk of a stream, carrying the assembled response."""

    response: ModelResponse


# What a Model yields while streaming: reasoning and text fragments interleaved,
# then exactly one StreamDone.
ModelChunk = TextDelta | ReasoningDelta | StreamDone
