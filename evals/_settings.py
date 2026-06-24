"""Eval-specific settings overrides."""

from __future__ import annotations

from dataclasses import replace

from milky_frog.settings import LangfuseSettings, Settings

_NO_LANGFUSE = LangfuseSettings(
    enabled=False,
    public_key=None,
    secret_key=None,
    host="https://cloud.langfuse.com",
)


def without_observability(settings: Settings) -> Settings:
    """Return settings with Langfuse disabled for headless eval runs."""
    return replace(settings, langfuse=_NO_LANGFUSE)
