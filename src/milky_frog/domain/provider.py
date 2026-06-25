from __future__ import annotations

from enum import StrEnum

_OPENAI_MODEL_PREFIXES = ("gpt", "o1", "o3", "o4", "chatgpt")


class Provider(StrEnum):
    """Model provider whose tokenizer (and, later, prompt conventions) apply.

    ``COMPATIBLE`` is the conservative default for any OpenAI-compatible
    gateway we do not recognise: it drives the approximate token counter, so
    behaviour is unchanged for unconfigured users.
    """

    OPENAI = "openai"
    DEEPSEEK = "deepseek"
    COMPATIBLE = "compatible"


def infer_provider(model: str | None, base_url: str | None) -> Provider:
    """Derive the provider from model name and base URL (convention over config).

    An explicit ``MILKY_FROG_PROVIDER`` overrides this; inference is only
    consulted when no provider is configured.
    """
    name = (model or "").lower()
    url = (base_url or "").lower()
    if name.startswith("deepseek") or "deepseek" in url:
        return Provider.DEEPSEEK
    if name.startswith(_OPENAI_MODEL_PREFIXES) or "openai" in url or base_url is None:
        return Provider.OPENAI
    return Provider.COMPATIBLE
