"""Token estimation model.

A request's prompt_tokens is approximated by an affine framing term over an
exact per-text count::

    count_messages(M)     = b + SUM[m in M]( a + SUM[str v in m] count_text(v) )
    count_tool_schemas(S) = SUM[s in S] count_text(json(s))
    count_text(t)         = exact BPE length                       (provider tokenizer)
                          ~ max(1, round(w_cjk*n_cjk + w_other*n_other))    (approx)

where a = per-message overhead, b = reply-priming overhead, and w_cjk / w_other
are per-character token weights; n_cjk / n_other count CJK vs other characters
in t. Only count_text differs per provider; the affine framing (a, b) and the
schema serialisation are shared. The residual gap to real prompt_tokens is left
to the workspace safety_margin (v2: fit a, b, w from usage).
"""

from __future__ import annotations

import json
from typing import Protocol

from pydantic import JsonValue

# a, b — OpenAI cookbook framing for current chat models (gpt-3.5-turbo-0613+,
# gpt-4*, gpt-4o*, o-series); the retired gpt-3.5-turbo-0301 used a = 4.
_PER_MESSAGE_OVERHEAD = 3  # a: per message
_REQUEST_OVERHEAD = 3  # b: reply priming, once per request
# w_cjk, w_other — DeepSeek offline prior: "1 Chinese char ≈ 0.6 token, 1 English
# char ≈ 0.3 token" (platform.deepseek.com → Token & Token Usage). CJK is ~2x
# denser, so per-class weights beat any single chars/token constant.
_TOKENS_PER_CJK_CHAR = 0.6  # w_cjk
_TOKENS_PER_OTHER_CHAR = 0.3  # w_other


def _is_cjk(ch: str) -> bool:
    """Whether ``ch`` is a CJK ideograph, kana, Hangul, or fullwidth/CJK form."""
    cp = ord(ch)
    return (
        0x3000 <= cp <= 0x303F  # CJK symbols & punctuation
        or 0x3040 <= cp <= 0x30FF  # Hiragana + Katakana
        or 0x3400 <= cp <= 0x4DBF  # CJK Unified Ext A
        or 0x4E00 <= cp <= 0x9FFF  # CJK Unified Ideographs
        or 0xAC00 <= cp <= 0xD7A3  # Hangul syllables
        or 0xF900 <= cp <= 0xFAFF  # CJK compatibility ideographs
        or 0xFF00 <= cp <= 0xFFEF  # halfwidth/fullwidth forms
    )


class TokenCounter(Protocol):
    """Protocol for counting tokens in model requests and responses."""

    def count_text(self, text: str) -> int: ...

    def count_messages(
        self, messages: list[dict[str, str]] | tuple[dict[str, str], ...]
    ) -> int: ...

    def count_tool_schemas(self, schemas: tuple[dict[str, JsonValue], ...]) -> int: ...


class BaseTokenCounter(TokenCounter):
    """Shared affine framing on top of a ``count_text`` primitive.

    Implements ``count_messages`` / ``count_tool_schemas`` from the module
    formula; subclasses provide only ``count_text``.
    """

    def count_text(self, text: str) -> int:
        raise NotImplementedError

    def count_messages(self, messages: list[dict[str, str]] | tuple[dict[str, str], ...]) -> int:
        total = _REQUEST_OVERHEAD
        for message in messages:
            total += _PER_MESSAGE_OVERHEAD
            for value in message.values():
                if isinstance(value, str):
                    total += self.count_text(value)
        return total

    def count_tool_schemas(self, schemas: tuple[dict[str, JsonValue], ...]) -> int:
        return sum(self.count_text(json.dumps(schema)) for schema in schemas)


class ApproxCharCounter(BaseTokenCounter):
    """CJK-aware char-weighted estimate; the default when no exact tokenizer is available.

    ``count_text(t) = max(1, round(w_cjk·n_cjk + w_other·n_other))``.
    """

    def count_text(self, text: str) -> int:
        if not text:
            return 0
        n_cjk = sum(1 for ch in text if _is_cjk(ch))
        n_other = len(text) - n_cjk
        return max(1, round(_TOKENS_PER_CJK_CHAR * n_cjk + _TOKENS_PER_OTHER_CHAR * n_other))
