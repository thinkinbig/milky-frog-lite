from __future__ import annotations

import json
from typing import Protocol

from pydantic import JsonValue

# Per-message overhead (~4 tokens for role/formatting).
_PER_MESSAGE_OVERHEAD = 4
# Rough characters-per-token ratio for the estimator.
_CHARS_PER_TOKEN = 4
# Fraction of a truncation budget kept from the head; the remainder is from the tail.
_HEAD_FRACTION = 0.35


class TokenCounter(Protocol):
    """Protocol for counting tokens in model requests and responses."""

    def count_text(self, text: str) -> int:
        """Count tokens in a text string."""
        ...

    def count_messages(self, messages: list[dict[str, str]] | tuple[dict[str, str], ...]) -> int:
        """Count tokens in a list of messages (role + content format)."""
        ...

    def count_tool_schemas(self, schemas: tuple[dict[str, JsonValue], ...]) -> int:
        """Count tokens used by tool/function definitions."""
        ...


class ApproxCharCounter:
    """Provider-agnostic token estimate (~4 chars per token).

    A single character-ratio estimator is enough because the ``BudgetHandler``
    anchors to reality: it learns a calibration factor from the provider's
    reported ``input_tokens`` and scales this estimate by it. That absorbs both
    the tokenizer difference and provider-side function-calling overhead, so an
    exact per-model tokenizer (e.g. tiktoken) buys nothing here.
    """

    def count_text(self, text: str) -> int:
        if not text:
            return 0
        return max(1, len(text) // _CHARS_PER_TOKEN)

    def count_messages(self, messages: list[dict[str, str]] | tuple[dict[str, str], ...]) -> int:
        total = 0
        for message in messages:
            total += _PER_MESSAGE_OVERHEAD
            for value in message.values():
                if isinstance(value, str):
                    total += self.count_text(value)
        return total

    def count_tool_schemas(self, schemas: tuple[dict[str, JsonValue], ...]) -> int:
        return sum(self.count_text(json.dumps(schema)) for schema in schemas)

    def truncate_text(self, text: str, *, limit_tokens: int) -> str:
        """Bound text to roughly ``limit_tokens`` with a head+tail window.

        Keeps the start and end of the output — the parts a model usually needs —
        and replaces the middle with a marker stating how much was dropped and how
        to retrieve it. Uses the same character-ratio estimate as ``BudgetHandler``
        so per-tool truncation and the conversation-wide budget reason about tokens
        with one ruler. Nothing is spilled to disk.
        """
        if limit_tokens <= 0 or self.count_text(text) <= limit_tokens:
            return text

        max_chars = limit_tokens * _CHARS_PER_TOKEN
        head_chars = int(max_chars * _HEAD_FRACTION)
        tail_chars = max_chars - head_chars

        head = text[:head_chars]
        tail = text[-tail_chars:]
        omitted_tokens = self.count_text(text[head_chars : len(text) - tail_chars])

        marker = (
            f"\n\n... [{omitted_tokens} tokens omitted — output truncated to "
            f"~{limit_tokens} tokens. Re-run a scoped command (e.g. `git diff <path>`) "
            f"or use read_file over a line range to see the omitted portion.] ...\n\n"
        )
        return head + marker + tail


def truncate_tool_output(text: str, *, limit_tokens: int) -> str:
    """Truncate tool output using the shared :class:`ApproxCharCounter` estimate."""
    return ApproxCharCounter().truncate_text(text, limit_tokens=limit_tokens)
