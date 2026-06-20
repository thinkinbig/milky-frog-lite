from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from milky_frog.domain import DEFAULT_MAX_MODEL_CALLS

PROJECT_DIRNAME = ".milky-frog"
CONFIG_FILENAME = "config.toml"

CONFIG_TEMPLATE = (
    "# Project-level Milky Frog configuration.\n"
    f"max_model_calls = {DEFAULT_MAX_MODEL_CALLS}\n"
)


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    """Declarative per-workspace settings read from .milky-frog/config.toml."""

    max_model_calls: int = DEFAULT_MAX_MODEL_CALLS


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
    if isinstance(max_model_calls, bool) or not isinstance(max_model_calls, int) or max_model_calls < 1:  # noqa: E501
        max_model_calls = DEFAULT_MAX_MODEL_CALLS
    return ProjectConfig(max_model_calls=max_model_calls)
