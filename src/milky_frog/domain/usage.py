from __future__ import annotations

from dataclasses import dataclass, field


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
