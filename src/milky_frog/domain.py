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


class ResumeError(Exception):
    """A Run cannot be advanced as requested."""


class ToolDecision(StrEnum):
    """Permission decision for a tool call before execution."""

    ALLOW = "allow"
    DENY = "deny"
    NEEDS_APPROVAL = "needs_approval"


class ApprovalDecision(StrEnum):
    """User's verdict on a Run paused for tool approval.

    Threaded into ``Harness.respond_approval`` to release pending tool calls:
    ``APPROVE`` executes them, ``DENY`` seals them with a refusal result.
    """

    APPROVE = "approve"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class ApprovalVerdict:
    """User's verdict on a Run paused for tool approval, optionally with a
    denial reason that is fed back to the agent.

    Used in place of a bare ``ApprovalDecision`` so the user can provide a
    reason when denying a tool call.
    """

    decision: ApprovalDecision
    denial_reason: str | None = None


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
class ToolResult:
    content: str
    is_error: bool = False


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
class TokenUsage:
    """Token counts reported for a single model call.

    ``input_tokens`` / ``output_tokens`` are the billed prompt and completion
    totals. ``cached_tokens`` is the subset of the input served from the
    provider's prompt cache, and ``reasoning_tokens`` the subset of the output
    spent on hidden reasoning (reasoning models). Providers that omit usage
    leave every field at zero — see :attr:`recorded`.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def recorded(self) -> bool:
        """Whether the provider actually reported usage for this call."""
        return self.total_tokens > 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )


@dataclass(frozen=True, slots=True)
class RunUsage:
    """Token totals accumulated across every model call in a Run.

    ``cumulative`` sums each call's usage — what the Run is billed for, since a
    chat-completions Run re-sends the whole conversation on every call.
    ``context_tokens`` is the most recent call's ``input_tokens``: the live
    conversation footprint, which is what matters for context-window pressure
    rather than the cumulative billed input.
    """

    cumulative: TokenUsage = field(default_factory=TokenUsage)
    context_tokens: int = 0

    @property
    def recorded(self) -> bool:
        return self.cumulative.recorded

    def record(self, call: TokenUsage) -> RunUsage:
        return RunUsage(
            cumulative=self.cumulative + call,
            context_tokens=call.input_tokens if call.recorded else self.context_tokens,
        )


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
    """A streamed fragment of a reasoning model's thinking (e.g. deepseek-reasoner).

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


DEFAULT_MAX_MODEL_CALLS = 30
# Default per-tool output cap (estimated tokens) applied before a result enters
# the transcript. Per-workspace override via ``tool_output_token_limit``.
DEFAULT_TOOL_OUTPUT_TOKEN_LIMIT = 10000


@dataclass(slots=True)
class RunCancellation:
    """Cooperative cancellation token for a foreground Run."""

    _cancelled: bool = field(default=False, repr=False)

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled


class ToolRunCancelled(Exception):
    """Cooperative cancel arrived while a Tool was executing."""


def is_cancelled(cancellation: RunCancellation | None) -> bool:
    return cancellation is not None and cancellation.is_cancelled


@dataclass(frozen=True, slots=True)
class RunRequest:
    prompt: str
    workspace: Path
    max_model_calls: int = DEFAULT_MAX_MODEL_CALLS
    tool_output_token_limit: int = DEFAULT_TOOL_OUTPUT_TOKEN_LIMIT
    cancellation: RunCancellation | None = None


@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: str
    status: RunStatus
    final_message: str
    model_calls: int
    usage: RunUsage = field(default_factory=RunUsage)


@dataclass(frozen=True, slots=True)
class RunState:
    """The live transcript and accounting of one Run, threaded through the loop.

    Also the durable Checkpoint snapshot: the Harness grows this value in memory and
    persists it after each meaningful step. ``resume`` loads the same shape rather
    than replaying an event log.
    """

    run_id: str
    workspace: Path
    messages: tuple[Message, ...] = ()
    completed_model_calls: int = 0
    reasoning_log: tuple[str, ...] = ()
    usage: RunUsage = field(default_factory=RunUsage)
