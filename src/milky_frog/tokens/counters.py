from __future__ import annotations

import logging
import urllib.request
from pathlib import Path
from typing import Any

from milky_frog.domain.provider import Provider
from milky_frog.tokens.base import ApproxCharCounter, BaseTokenCounter, TokenCounter

logger = logging.getLogger(__name__)

# Hugging Face repos whose ``tokenizer.json`` reproduces a provider's BPE.
_DEEPSEEK_REPO = "deepseek-ai/DeepSeek-V3"
_FETCH_TIMEOUT_SECONDS = 30


class TiktokenCounter(BaseTokenCounter):
    """Exact OpenAI token counts via :mod:`tiktoken` (optional dependency)."""

    def __init__(self, model: str | None) -> None:  # pragma: no cover - needs tiktoken
        import tiktoken  # optional: pip install 'milky-frog[openai-tokenizer]'

        try:
            self._encoding = tiktoken.encoding_for_model(model or "")
        except KeyError:
            # Unmapped / newer model names fall back to the current base encoding.
            self._encoding = tiktoken.get_encoding("o200k_base")

    def count_text(self, text: str) -> int:  # pragma: no cover - needs tiktoken
        if not text:
            return 0
        return len(self._encoding.encode(text, disallowed_special=()))


class HFTokenizerCounter(BaseTokenCounter):
    """Exact counts from a Hugging Face ``tokenizer.json`` via :mod:`tokenizers`."""

    def __init__(self, tokenizer: Any) -> None:
        self._tokenizer = tokenizer

    def count_text(self, text: str) -> int:
        if not text:
            return 0
        return len(self._tokenizer.encode(text, add_special_tokens=False).ids)


def make_token_counter(provider: Provider, model: str | None, *, cache_dir: Path) -> TokenCounter:
    """Select a provider-specific exact counter, degrading to approximate.

    The optional tokenizer packages (``tiktoken`` / ``tokenizers``) and any
    network fetch of tokenizer data are best-effort: on a missing dependency,
    an offline failure, or an unrecognised provider, the approximate counter is
    returned (with a warning) so a Run is never blocked on token counting.
    """
    try:
        if provider is Provider.OPENAI:
            return TiktokenCounter(model)
        if provider is Provider.DEEPSEEK:
            return HFTokenizerCounter(_load_hf_tokenizer(_DEEPSEEK_REPO, cache_dir))
    except Exception as exc:
        logger.warning(
            "exact token counter for provider %s unavailable (%s); using approximate counts",
            provider,
            exc,
        )
    return ApproxCharCounter()


def _load_hf_tokenizer(repo: str, cache_dir: Path) -> Any:  # pragma: no cover - needs tokenizers
    from tokenizers import Tokenizer  # optional: pip install 'milky-frog[deepseek-tokenizer]'

    path = cache_dir / f"{repo.replace('/', '_')}.json"
    if not path.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        url = f"https://huggingface.co/{repo}/resolve/main/tokenizer.json"
        with urllib.request.urlopen(url, timeout=_FETCH_TIMEOUT_SECONDS) as response:
            data = response.read()
        path.write_bytes(data)
    return Tokenizer.from_file(str(path))
