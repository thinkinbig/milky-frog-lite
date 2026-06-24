from __future__ import annotations

import json
from typing import TYPE_CHECKING, Protocol

from pydantic import JsonValue

if TYPE_CHECKING:
    pass


class TokenCounter(Protocol):
    """Protocol for counting tokens in model requests and responses."""

    def count_text(self, text: str) -> int:
        """Count tokens in a text string."""
        ...

    def count_messages(
        self, messages: list[dict[str, str]] | tuple[dict[str, str], ...]
    ) -> int:
        """Count tokens in a list of messages (role + content format)."""
        ...

    def count_tool_schemas(self, schemas: tuple[dict[str, JsonValue], ...]) -> int:
        """Count tokens used by tool/function definitions."""
        ...


class TiktokenCounter:
    """Token counter using tiktoken encoding."""

    def __init__(self, model: str) -> None:
        try:
            import tiktoken as tiktoken_module
        except ImportError as e:
            raise ImportError(
                "tiktoken is required for token counting. "
                "Install it with: uv add tiktoken"
            ) from e

        self._model = model
        self._encoding = self._get_encoding(model, tiktoken_module)

    def _get_encoding(
        self, model: str, tiktoken_module: object
    ) -> object:
        """Get the appropriate encoding for the model.

        Uses o200k_base for newer models, cl100k_base for older ones.
        """
        if any(newer in model.lower() for newer in ("o1", "o3", "gpt-4o", "gpt-4-turbo")):
            return tiktoken_module.get_encoding("o200k_base")  # type: ignore[attr-defined]
        return tiktoken_module.get_encoding("cl100k_base")  # type: ignore[attr-defined]

    def count_text(self, text: str) -> int:
        """Count tokens in a text string."""
        return len(self._encoding.encode(text))  # type: ignore[attr-defined]

    def count_messages(
        self, messages: list[dict[str, str]] | tuple[dict[str, str], ...]
    ) -> int:
        """Count tokens in messages, including per-message role/format overhead.

        Each message has ~4 tokens of overhead for the role and formatting.
        """
        total = 0
        for message in messages:
            total += 4
            for value in message.values():
                if isinstance(value, str):
                    total += self.count_text(value)
        return total

    def count_tool_schemas(self, schemas: tuple[dict[str, JsonValue], ...]) -> int:
        """Count tokens in tool/function definitions.

        Schemas are serialized as JSON and included in the request.
        """
        total = 0
        for schema in schemas:
            schema_json = json.dumps(schema)
            total += self.count_text(schema_json)
        return total
