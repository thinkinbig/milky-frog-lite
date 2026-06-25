from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from milky_frog.domain import DEFAULT_MAX_MODEL_CALLS

PROJECT_DIRNAME = ".milky-frog"
CONFIG_FILENAME = "config.toml"

DEFAULT_CONTEXT_WINDOW = 128000
DEFAULT_OUTPUT_RESERVE = 8000
DEFAULT_SAFETY_MARGIN = 32000
DEFAULT_BASH_TIMEOUT_SECONDS = 60
DEFAULT_RETENTION_DAYS = 30
DEFAULT_BASH_OUTPUT_MAX_CHARS = 128000
DEFAULT_READ_OUTPUT_MAX_CHARS = 64000
DEFAULT_SEARCH_OUTPUT_MAX_CHARS = 32000

CONFIG_TEMPLATE = (
    f"# Project-level Milky Frog configuration.\n"
    f"max_model_calls = {DEFAULT_MAX_MODEL_CALLS}\n\n"
    f"# Context window budgeting (trim before each model call).\n"
    f"# context_window = {DEFAULT_CONTEXT_WINDOW}\n"
    f"# output_reserve = {DEFAULT_OUTPUT_RESERVE}\n"
    f"# safety_margin absorbs token-counting drift; with an exact provider\n"
    f"# tokenizer (MILKY_FROG_PROVIDER=openai/deepseek) you can lower it.\n"
    f"# safety_margin = {DEFAULT_SAFETY_MARGIN}\n"
    f"\n"
    f"# Tool output truncation limits (characters returned to the model).\n"
    f"# bash_output_max_chars = {DEFAULT_BASH_OUTPUT_MAX_CHARS}\n"
    f"# read_output_max_chars = {DEFAULT_READ_OUTPUT_MAX_CHARS}\n"
    f"# search_output_max_chars = {DEFAULT_SEARCH_OUTPUT_MAX_CHARS}\n"
    f"\n"
    f"[checkpoint]\n"
    f"retention_days = 30\n"
    f"prune_on_start = true\n"
    f"\n"
    f"# Additional host env var names forwarded to subprocesses (uppercase identifiers).\n"
    f'# env_allowlist_extra = ["MY_BUILD_VAR", "DEPLOY_TOKEN"]\n'
)


class CheckpointConfig(BaseModel):
    """Retention and pruning policy for checkpoint snapshots."""

    model_config = ConfigDict(frozen=True)

    retention_days: int = Field(default=DEFAULT_RETENTION_DAYS, ge=0)
    prune_on_start: bool = True


class ProjectConfig(BaseModel):
    """Per-workspace settings read from ``.milky-frog/config.toml``.

    A missing or malformed file yields defaults rather than raising, so a
    broken config never blocks a Run.
    """

    model_config = ConfigDict(frozen=True)

    max_model_calls: int = Field(default=DEFAULT_MAX_MODEL_CALLS, ge=1)
    context_window: int = Field(default=DEFAULT_CONTEXT_WINDOW, ge=1000)
    output_reserve: int = Field(default=DEFAULT_OUTPUT_RESERVE, ge=100)
    safety_margin: int = Field(default=DEFAULT_SAFETY_MARGIN, ge=0)
    bash_timeout_seconds: int = Field(default=DEFAULT_BASH_TIMEOUT_SECONDS, ge=1, le=600)
    bash_output_max_chars: int = Field(default=DEFAULT_BASH_OUTPUT_MAX_CHARS, ge=1000)
    read_output_max_chars: int = Field(default=DEFAULT_READ_OUTPUT_MAX_CHARS, ge=1000)
    search_output_max_chars: int = Field(default=DEFAULT_SEARCH_OUTPUT_MAX_CHARS, ge=1000)
    env_allowlist_extra: tuple[str, ...] = ()
    checkpoint: CheckpointConfig = CheckpointConfig()

    # ── Validators ────────────────────────────────────────────────

    @field_validator(
        "max_model_calls",
        "context_window",
        "output_reserve",
        "safety_margin",
        "bash_timeout_seconds",
        "bash_output_max_chars",
        "read_output_max_chars",
        "search_output_max_chars",
        mode="before",
    )
    @classmethod
    def _reject_bool_for_int_fields(cls, v: object) -> object:
        """Reject boolean values masquerading as integers.

        Python's ``bool`` is a subclass of ``int``, so pydantic would
        happily coerce ``True`` → ``1`` without this check.
        """
        if isinstance(v, bool):
            raise ValueError(
                f"boolean value is not allowed for integer field; expected an integer, got {v!r}"
            )
        return v

    @field_validator("env_allowlist_extra", mode="before")
    @classmethod
    def _validate_env_allowlist_extra(cls, v: object) -> tuple[str, ...]:
        """Keep only entries that are valid uppercase Python identifiers."""
        if not isinstance(v, (list, tuple)):
            return ()
        return tuple(
            item for item in v if isinstance(item, str) and item.isidentifier() and item.isupper()
        )


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
    try:
        return ProjectConfig.model_validate(data)
    except ValidationError:
        return ProjectConfig()
