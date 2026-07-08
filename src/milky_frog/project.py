from __future__ import annotations

import tomllib
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from milky_frog.domain import DEFAULT_MAX_MODEL_CALLS

PROJECT_DIRNAME = ".milky-frog"
CONFIG_FILENAME = "config.toml"

DEFAULT_CONTEXT_WINDOW = 128000
DEFAULT_OUTPUT_RESERVE = 8000
DEFAULT_SAFETY_MARGIN = 32000
DEFAULT_BASH_TIMEOUT_SECONDS = 60
DEFAULT_FETCH_TIMEOUT_SECONDS = 30
DEFAULT_WEB_SEARCH_TIMEOUT_SECONDS = 45
DEFAULT_RETENTION_DAYS = 30
DEFAULT_BASH_OUTPUT_MAX_CHARS = 128000
DEFAULT_FETCH_OUTPUT_MAX_CHARS = 100000
DEFAULT_WEB_SEARCH_OUTPUT_MAX_CHARS = 20000
DEFAULT_READ_OUTPUT_MAX_CHARS = 64000
DEFAULT_SEARCH_OUTPUT_MAX_CHARS = 32000
DEFAULT_SUMMARIZATION_TRIGGER_TOKENS = 96000
DEFAULT_SUMMARIZATION_KEEP_RECENT_TOKENS = 32000
DEFAULT_WORKSPACE_MOUNT = "/mnt/workspace"

# Host-built, architecture-specific directories that live inside a Workspace.
# The bind mount would otherwise carry them into the container, where a macOS
# `.venv/bin/python` or a natively-compiled `node_modules` is worse than absent:
# a model, finding no toolchain, reaches for them and gets a misleading failure
# (`ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'`) that
# looks like a broken sandbox. Masking makes them plainly empty instead.
DEFAULT_MASK_PATHS: tuple[str, ...] = (".venv", "node_modules")

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
    f"# fetch_output_max_chars = {DEFAULT_FETCH_OUTPUT_MAX_CHARS}\n"
    f"\n"
    f"# fetch Tool: outbound HTTP GET timeout (seconds). Loopback/private hosts\n"
    f"# are blocked to prevent SSRF; every fetch requires approval.\n"
    f"# fetch_timeout_seconds = {DEFAULT_FETCH_TIMEOUT_SECONDS}\n"
    f"\n"
    f"# web_search Tool: only registered when MILKY_FROG_JINA_API_KEY is set.\n"
    f"# web_search_timeout_seconds = {DEFAULT_WEB_SEARCH_TIMEOUT_SECONDS}\n"
    f"# web_search_output_max_chars = {DEFAULT_WEB_SEARCH_OUTPUT_MAX_CHARS}\n"
    f"\n"
    f"# Conversation summarization (compaction): when the transcript exceeds the\n"
    f"# trigger, older rounds are summarized away from the model request (the full\n"
    f"# transcript is still kept in the snapshot).\n"
    f"summarization_enabled = true\n"
    f"summarization_trigger_tokens = {DEFAULT_SUMMARIZATION_TRIGGER_TOKENS}\n"
    f"summarization_keep_recent_tokens = {DEFAULT_SUMMARIZATION_KEEP_RECENT_TOKENS}\n"
    f"\n"
    f"[checkpoint]\n"
    f"retention_days = 30\n"
    f"prune_on_start = true\n"
    f"\n"
    f"[verification]\n"
    f"after_edit = true\n"
    f'commands = ["uv run ruff check .", "uv run pytest -q"]\n'
    f"\n"
    f"# Additional host env var names forwarded to subprocesses (uppercase identifiers).\n"
    f'# env_allowlist_extra = ["MY_BUILD_VAR", "DEPLOY_TOKEN"]\n'
    f"\n"
    f"# Execution Sandbox. 'local' runs Tools on the host under the path-deny\n"
    f"# policy. 'docker' bind-mounts the workspace into a container and runs\n"
    f"# bash + verification commands there (requires the docker CLI on PATH).\n"
    f"# [sandbox]\n"
    f'# kind = "docker"\n'
    f'# image = "python:3.12-bookworm"\n'
    f'# workspace_mount = "{DEFAULT_WORKSPACE_MOUNT}"  # must live under /mnt\n'
)


class SandboxConfigError(ValueError):
    """Raised when the ``[sandbox]`` table is present but invalid.

    Unlike the rest of ``config.toml`` — where a malformed value silently
    yields defaults so a broken file never blocks a Run — a broken
    ``[sandbox]`` table must fail loudly. Silently falling back to
    ``LocalSandbox`` would leave a user who asked for container isolation
    running unsandboxed on the host with no signal.
    """


class SandboxConfig(BaseModel):
    """Which Sandbox adapter a Workspace uses, and how to build it.

    ``local`` (the default) runs Tools on the host under the path-deny policy.
    ``docker`` bind-mounts the Workspace into a container and runs commands
    there; it requires an ``image``.

    Unknown keys are rejected. A silently-ignored ``workspace = "/mnt/foo"``
    (the field is ``workspace_mount``) would leave the user believing they had
    configured a mount they had not — the same class of quiet wrongness this
    section's loud validation exists to prevent.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["local", "docker"] = "local"
    image: str | None = None
    workspace_mount: str = DEFAULT_WORKSPACE_MOUNT
    mask_paths: tuple[str, ...] = DEFAULT_MASK_PATHS

    @field_validator("mask_paths")
    @classmethod
    def _require_relative_mask_paths(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        for path in v:
            if not path or path.startswith("/"):
                raise ValueError(
                    f"mask_paths entries must be relative to the workspace, got {path!r}"
                )
            normalized = PurePosixPath(path)
            if ".." in normalized.parts or str(normalized) == ".":
                raise ValueError(f"mask_paths entries must stay inside the workspace, got {path!r}")
        return v

    @field_validator("workspace_mount")
    @classmethod
    def _require_mnt_prefix(cls, v: str) -> str:
        # Require absolute path
        if not v.startswith("/"):
            raise ValueError(f"workspace_mount must be /mnt or under /mnt/, got {v!r}")
        # Reject paths with upward traversals
        normalized = PurePosixPath(v)
        if ".." in normalized.parts:
            raise ValueError(f"workspace_mount must be /mnt or under /mnt/, got {v!r}")
        # Check normalized path is /mnt or under /mnt/
        if str(normalized) != "/mnt" and not str(normalized).startswith("/mnt/"):
            raise ValueError(f"workspace_mount must be /mnt or under /mnt/, got {v!r}")
        return v

    @model_validator(mode="after")
    def _require_image_for_docker(self) -> SandboxConfig:
        if self.kind == "docker" and not self.image:
            raise ValueError("image is required when sandbox.kind = 'docker'")
        return self


class CheckpointConfig(BaseModel):
    """Retention and pruning policy for checkpoint snapshots."""

    model_config = ConfigDict(frozen=True)

    retention_days: int = Field(default=DEFAULT_RETENTION_DAYS, ge=0)
    prune_on_start: bool = True


class VerificationConfig(BaseModel):
    """Post-edit verification policy.

    When ``after_edit`` is true, the Harness runs ``commands`` sequentially
    after every successful ``edit_file`` / ``write_file`` call. Results are
    injected into the transcript; failures do not block the loop.
    """

    model_config = ConfigDict(frozen=True)

    after_edit: bool = True
    commands: tuple[str, ...] = (
        "uv run ruff check .",
        "uv run pytest -q",
    )


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
    fetch_timeout_seconds: int = Field(default=DEFAULT_FETCH_TIMEOUT_SECONDS, ge=1, le=600)
    web_search_timeout_seconds: int = Field(
        default=DEFAULT_WEB_SEARCH_TIMEOUT_SECONDS, ge=1, le=600
    )
    bash_output_max_chars: int = Field(default=DEFAULT_BASH_OUTPUT_MAX_CHARS, ge=1000)
    read_output_max_chars: int = Field(default=DEFAULT_READ_OUTPUT_MAX_CHARS, ge=1000)
    search_output_max_chars: int = Field(default=DEFAULT_SEARCH_OUTPUT_MAX_CHARS, ge=1000)
    fetch_output_max_chars: int = Field(default=DEFAULT_FETCH_OUTPUT_MAX_CHARS, ge=1000)
    web_search_output_max_chars: int = Field(default=DEFAULT_WEB_SEARCH_OUTPUT_MAX_CHARS, ge=1000)
    summarization_enabled: bool = True
    summarization_trigger_tokens: int = Field(default=DEFAULT_SUMMARIZATION_TRIGGER_TOKENS, ge=1000)
    summarization_keep_recent_tokens: int = Field(
        default=DEFAULT_SUMMARIZATION_KEEP_RECENT_TOKENS, ge=1000
    )
    env_allowlist_extra: tuple[str, ...] = ()
    checkpoint: CheckpointConfig = CheckpointConfig()
    verification: VerificationConfig = VerificationConfig()
    sandbox: SandboxConfig = SandboxConfig()

    # ── Validators ────────────────────────────────────────────────

    @field_validator(
        "max_model_calls",
        "context_window",
        "output_reserve",
        "safety_margin",
        "bash_timeout_seconds",
        "fetch_timeout_seconds",
        "web_search_timeout_seconds",
        "bash_output_max_chars",
        "read_output_max_chars",
        "search_output_max_chars",
        "fetch_output_max_chars",
        "web_search_output_max_chars",
        "summarization_trigger_tokens",
        "summarization_keep_recent_tokens",
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


def _read_config_data(workspace: Path) -> dict[str, object] | None:
    path = project_root(workspace) / CONFIG_FILENAME
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None


def load_project_config(workspace: Path) -> ProjectConfig:
    """Read ``<workspace>/.milky-frog/config.toml``; fall back to defaults.

    A missing or malformed file yields defaults rather than raising, so a
    broken config never blocks a Run. Callers that must not silently accept a
    broken ``[sandbox]`` table call ``validate_sandbox_config`` first.
    """
    data = _read_config_data(workspace)
    if data is None:
        return ProjectConfig()
    try:
        return ProjectConfig.model_validate(data)
    except ValidationError:
        return ProjectConfig()


_VALUE_ERROR_PREFIX = "Value error, "


def _compact_validation_message(error: ValidationError) -> str:
    """Join pydantic error messages, stripping the ``str(ValidationError)`` noise.

    ``str(error)`` drags in "N validation error(s) for SandboxConfig", an
    indented "Value error," prefix per message, and a
    "https://errors.pydantic.dev/..." link — none of which helps a user fix
    their TOML. ``error.errors()[i]["msg"]`` is the human-readable part.

    The field is prefixed when pydantic knows one. Without it, an unknown key
    reports only "Extra inputs are not permitted", which names neither the key
    at fault nor the key that was meant — loud, but not actionable. Whole-model
    validators carry an empty ``loc`` and are reported unprefixed.
    """
    messages = []
    for item in error.errors():
        msg = item["msg"]
        if msg.startswith(_VALUE_ERROR_PREFIX):
            msg = msg[len(_VALUE_ERROR_PREFIX) :]
        location = ".".join(str(part) for part in item["loc"])
        messages.append(f"{location}: {msg}" if location else msg)
    return "; ".join(messages)


def validate_sandbox_config(workspace: Path) -> None:
    """Raise ``SandboxConfigError`` if the ``[sandbox]`` table is invalid.

    Called once at startup (CLI entry, ``doctor``). ``load_project_config`` is
    deliberately left lenient because it runs on per-step hot paths where
    raising would abort a Run mid-flight.
    """
    data = _read_config_data(workspace)
    if data is None or "sandbox" not in data:
        return
    try:
        SandboxConfig.model_validate(data["sandbox"])
    except ValidationError as error:
        message = _compact_validation_message(error)
        raise SandboxConfigError(f"invalid [sandbox] in {CONFIG_FILENAME}: {message}") from error
