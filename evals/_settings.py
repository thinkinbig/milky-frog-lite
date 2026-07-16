"""Eval-specific settings overrides."""

from __future__ import annotations

from pathlib import Path

from milky_frog.settings import Settings


def without_observability(settings: Settings) -> Settings:
    """Return settings with Langfuse disabled for headless eval runs.

    ``Settings`` is a Pydantic ``BaseSettings`` whose ``langfuse`` attribute is a
    derived property, not a field — so we toggle the flat ``langfuse_enabled``
    flag it is assembled from rather than replacing the whole object.
    """
    return settings.model_copy(update={"langfuse_enabled": False})


def with_pinned_home(settings: Settings, home: Path) -> Settings:
    """Point ``home`` at a controlled dir so no user config leaks into a Run.

    MCP servers, Skills, and Memory are all sourced from ``home`` (and the
    workspace) — never from code. An empty, dedicated eval home therefore yields
    the *pure built-in Harness with built-in Tools*, identical across machines.
    Without this, whatever MCP server / Skill / Memory the operator happens to
    have in ``~/.milky-frog`` silently becomes part of the measured Harness.
    """
    return settings.model_copy(update={"home": home})
