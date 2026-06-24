from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from milky_frog.domain import DEFAULT_MAX_MODEL_CALLS

PROJECT_DIRNAME = ".milky-frog"
CONFIG_FILENAME = "config.toml"

CONFIG_TEMPLATE = (
    f"# Project-level Milky Frog configuration.\n"
    f"max_model_calls = {DEFAULT_MAX_MODEL_CALLS}\n\n"
    f"[checkpoint]\n"
    f"retention_days = 30\n"
    f"prune_on_start = true\n"
    f"\n"
    f"# Additional host env var names forwarded to subprocesses (uppercase identifiers).\n"
    f'# env_allowlist_extra = ["MY_BUILD_VAR", "DEPLOY_TOKEN"]\n'
)

DEFAULT_CONTEXT_WINDOW = 128000
DEFAULT_OUTPUT_RESERVE = 8000
DEFAULT_SAFETY_MARGIN = 1000
DEFAULT_BASH_TIMEOUT_SECONDS = 60
DEFAULT_RETENTION_DAYS = 30


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    """Declarative per-workspace settings read from .milky-frog/config.toml."""

    max_model_calls: int = DEFAULT_MAX_MODEL_CALLS
    context_window: int = DEFAULT_CONTEXT_WINDOW
    output_reserve: int = DEFAULT_OUTPUT_RESERVE
    safety_margin: int = DEFAULT_SAFETY_MARGIN
    bash_timeout_seconds: int = DEFAULT_BASH_TIMEOUT_SECONDS
    checkpoint_retention_days: int = DEFAULT_RETENTION_DAYS
    prune_on_start: bool = True
    env_allowlist_extra: tuple[str, ...] = ()


def project_root(workspace: Path) -> Path:
    return workspace / PROJECT_DIRNAME


def load_project_config(workspace: Path) -> ProjectConfig:
    """Read ``<workspace>/.milky-frog/config.toml``; fall back to defaults.

    A missing or malformed file yields defaults rather than raising, so a
    broken config never blocks a Run.
    """
    path = project_root(workspace) / CONFIG_FILENAME
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return ProjectConfig()

    max_model_calls = data.get("max_model_calls", DEFAULT_MAX_MODEL_CALLS)
    if (
        isinstance(max_model_calls, bool)
        or not isinstance(max_model_calls, int)
        or max_model_calls < 1
    ):
        max_model_calls = DEFAULT_MAX_MODEL_CALLS

    context_window = data.get("context_window", DEFAULT_CONTEXT_WINDOW)
    if (
        isinstance(context_window, bool)
        or not isinstance(context_window, int)
        or context_window < 1000
    ):
        context_window = DEFAULT_CONTEXT_WINDOW

    output_reserve = data.get("output_reserve", DEFAULT_OUTPUT_RESERVE)
    if (
        isinstance(output_reserve, bool)
        or not isinstance(output_reserve, int)
        or output_reserve < 100
    ):
        output_reserve = DEFAULT_OUTPUT_RESERVE

    safety_margin = data.get("safety_margin", DEFAULT_SAFETY_MARGIN)
    if isinstance(safety_margin, bool) or not isinstance(safety_margin, int) or safety_margin < 0:
        safety_margin = DEFAULT_SAFETY_MARGIN

    bash_timeout_seconds = data.get("bash_timeout_seconds", DEFAULT_BASH_TIMEOUT_SECONDS)
    if (
        isinstance(bash_timeout_seconds, bool)
        or not isinstance(bash_timeout_seconds, int)
        or bash_timeout_seconds < 1
        or bash_timeout_seconds > 600
    ):
        bash_timeout_seconds = DEFAULT_BASH_TIMEOUT_SECONDS

    # ── [checkpoint] section ─────────────────────────────────────────
    cp = data.get("checkpoint", {})
    if not isinstance(cp, dict):
        cp = {}

    checkpoint_retention_days = cp.get("retention_days", DEFAULT_RETENTION_DAYS)
    if (
        isinstance(checkpoint_retention_days, bool)
        or not isinstance(checkpoint_retention_days, int)
        or checkpoint_retention_days < 0
    ):
        checkpoint_retention_days = DEFAULT_RETENTION_DAYS

    prune_on_start = cp.get("prune_on_start", True)
    if not isinstance(prune_on_start, bool):
        prune_on_start = True

    env_allowlist_extra_raw = data.get("env_allowlist_extra", ())
    if not isinstance(env_allowlist_extra_raw, list):
        env_allowlist_extra_raw = ()
    env_allowlist_extra: tuple[str, ...] = tuple(
        str(v)
        for v in env_allowlist_extra_raw
        if isinstance(v, str) and v.isidentifier() and v.isupper()
    )

    return ProjectConfig(
        max_model_calls=max_model_calls,
        context_window=context_window,
        output_reserve=output_reserve,
        safety_margin=safety_margin,
        bash_timeout_seconds=bash_timeout_seconds,
        checkpoint_retention_days=checkpoint_retention_days,
        prune_on_start=prune_on_start,
        env_allowlist_extra=env_allowlist_extra,
    )
