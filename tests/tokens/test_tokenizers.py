from __future__ import annotations

from pathlib import Path

import pytest

from milky_frog.domain.provider import Provider, infer_provider
from milky_frog.settings import Settings
from milky_frog.tokens import ApproxCharCounter, BaseTokenCounter, make_token_counter
from milky_frog.tokens import counters as counters_module
from milky_frog.tokens.base import _PER_MESSAGE_OVERHEAD, _REQUEST_OVERHEAD
from milky_frog.tokens.counters import HFTokenizerCounter


class _OneTokenCounter(BaseTokenCounter):
    """count_text returns 1 per non-empty string, to prove shared framing uses it."""

    def count_text(self, text: str) -> int:
        return 1 if text else 0


class _FakeEncoding:
    def __init__(self, ids: list[int]) -> None:
        self.ids = ids


class _FakeTokenizer:
    """Stands in for a ``tokenizers.Tokenizer`` (one id per character)."""

    def encode(self, text: str, *, add_special_tokens: bool) -> _FakeEncoding:
        return _FakeEncoding(list(range(len(text))))


# ── Shared framing ────────────────────────────────────────────────────


def test_base_counter_framing_builds_on_count_text() -> None:
    counter = _OneTokenCounter()
    # one message with role + content (2 non-empty strings) plus the framing.
    expected = _REQUEST_OVERHEAD + _PER_MESSAGE_OVERHEAD + 2
    assert counter.count_messages([{"role": "user", "content": "hi"}]) == expected
    assert counter.count_tool_schemas(({"name": "x"},)) == 1


# ── Provider inference (convention over configuration) ─────────────────


@pytest.mark.parametrize(
    ("model", "base_url", "expected"),
    [
        ("deepseek-chat", "https://api.deepseek.com", Provider.DEEPSEEK),
        ("deepseek-reasoner", None, Provider.DEEPSEEK),
        ("gpt-4o", None, Provider.OPENAI),
        ("o3-mini", None, Provider.OPENAI),
        ("anything", "https://api.openai.com/v1", Provider.OPENAI),
        (None, None, Provider.OPENAI),
        ("local-model", "http://localhost:8000/v1", Provider.COMPATIBLE),
        ("qwen", "https://api.deepseek.com", Provider.DEEPSEEK),
    ],
)
def test_infer_provider(model: str | None, base_url: str | None, expected: Provider) -> None:
    assert infer_provider(model, base_url) == expected


def test_settings_resolved_provider_prefers_explicit_override() -> None:
    settings = Settings(model="gpt-4o", api_key="k", provider="deepseek", _env_file=None)
    assert settings.resolved_provider is Provider.DEEPSEEK


def test_settings_resolved_provider_infers_when_unset() -> None:
    settings = Settings(model="deepseek-chat", api_key="k", _env_file=None)
    assert settings.resolved_provider is Provider.DEEPSEEK


def test_settings_resolved_provider_falls_back_on_unknown_override() -> None:
    settings = Settings(model="gpt-4o", api_key="k", provider="bogus", _env_file=None)
    assert settings.resolved_provider is Provider.OPENAI


# ── Counter selection and graceful degrade ────────────────────────────


def test_compatible_provider_uses_approx(tmp_path: Path) -> None:
    counter = make_token_counter(Provider.COMPATIBLE, "whatever", cache_dir=tmp_path)
    assert isinstance(counter, ApproxCharCounter)


def test_unavailable_tokenizer_degrades_to_approx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(_model: str | None) -> None:
        raise ImportError("tiktoken is not installed")

    monkeypatch.setattr(counters_module, "TiktokenCounter", _raise)
    counter = make_token_counter(Provider.OPENAI, "gpt-4o", cache_dir=tmp_path)
    assert isinstance(counter, ApproxCharCounter)


def test_hf_counter_counts_via_tokenizer_ids() -> None:
    counter = HFTokenizerCounter(_FakeTokenizer())
    assert counter.count_text("") == 0
    assert counter.count_text("hello") == 5
