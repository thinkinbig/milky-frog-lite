from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from milky_frog.domain import DEFAULT_MAX_MODEL_CALLS

PROJECT_DIRNAME = ".milky-frog"
CONFIG_FILENAME = "config.toml"

CONFIG_TEMPLATE = (
    f"# Project-level Milky Frog configuration.\nmax_model_calls = {DEFAULT_MAX_MODEL_CALLS}\n"
)

DEFAULT_CONTEXT_WINDOW = 128000
DEFAULT_OUTPUT_RESERVE = 8000
DEFAULT_SAFETY_MARGIN = 1000


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    """Declarative per-workspace settings read from .milky-frog/config.toml."""

    max_model_calls: int = DEFAULT_MAX_MODEL_CALLS
    context_window: int = DEFAULT_CONTEXT_WINDOW
    output_reserve: int = DEFAULT_OUTPUT_RESERVE
    safety_margin: int = DEFAULT_SAFETY_MARGIN


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
    if (
        isinstance(safety_margin, bool)
        or not isinstance(safety_margin, int)
        or safety_margin < 0
    ):
        safety_margin = DEFAULT_SAFETY_MARGIN

    return ProjectConfig(
        max_model_calls=max_model_calls,
        context_window=context_window,
        output_reserve=output_reserve,
        safety_margin=safety_margin,
    )
