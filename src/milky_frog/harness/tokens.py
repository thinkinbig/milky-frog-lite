from __future__ import annotations

import json
from typing import Protocol

from pydantic import JsonValue

# Per-message overhead (~4 tokens for role/formatting).
_PER_MESSAGE_OVERHEAD = 4
# Rough characters-per-token ratio for the estimator.
_CHARS_PER_TOKEN = 4


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
