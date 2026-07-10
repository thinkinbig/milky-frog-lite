from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from milky_frog.domain import DEFAULT_MAX_MODEL_CALLS
from milky_frog.project import (
    CONFIG_FILENAME,
    CONFIG_TEMPLATE,
    DEFAULT_BASH_TIMEOUT_SECONDS,
    DEFAULT_WEB_SEARCH_TIMEOUT_SECONDS,
    PROJECT_DIRNAME,
    SandboxConfig,
    SandboxConfigError,
    load_project_config,
    validate_sandbox_config,
)


def _write_config(workspace: Path, body: str) -> None:
    root = workspace / PROJECT_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    (root / CONFIG_FILENAME).write_text(body, encoding="utf-8")


def test_reads_max_model_calls_from_config(tmp_path: Path) -> None:
    _write_config(tmp_path, "max_model_calls = 7\n")

    assert load_project_config(tmp_path).max_model_calls == 7


def test_missing_config_uses_default(tmp_path: Path) -> None:
    assert load_project_config(tmp_path).max_model_calls == DEFAULT_MAX_MODEL_CALLS


def test_malformed_config_falls_back_to_default(tmp_path: Path) -> None:
    _write_config(tmp_path, "max_model_calls = not-a-number\n")

    assert load_project_config(tmp_path).max_model_calls == DEFAULT_MAX_MODEL_CALLS


def test_non_positive_value_falls_back_to_default(tmp_path: Path) -> None:
    _write_config(tmp_path, "max_model_calls = 0\n")

    assert load_project_config(tmp_path).max_model_calls == DEFAULT_MAX_MODEL_CALLS


def test_generated_template_round_trips(tmp_path: Path) -> None:
    _write_config(tmp_path, CONFIG_TEMPLATE)

    assert load_project_config(tmp_path).max_model_calls == DEFAULT_MAX_MODEL_CALLS


def test_reads_bash_timeout_seconds_from_config(tmp_path: Path) -> None:
    _write_config(tmp_path, "bash_timeout_seconds = 600\n")

    assert load_project_config(tmp_path).bash_timeout_seconds == 600


def test_missing_config_uses_default_bash_timeout(tmp_path: Path) -> None:
    assert load_project_config(tmp_path).bash_timeout_seconds == DEFAULT_BASH_TIMEOUT_SECONDS


def test_invalid_bash_timeout_falls_back_to_default(tmp_path: Path) -> None:
    _write_config(tmp_path, "bash_timeout_seconds = 0\n")

    assert load_project_config(tmp_path).bash_timeout_seconds == DEFAULT_BASH_TIMEOUT_SECONDS


def test_reads_web_search_timeout_seconds_from_config(tmp_path: Path) -> None:
    _write_config(tmp_path, "web_search_timeout_seconds = 45\n")

    assert load_project_config(tmp_path).web_search_timeout_seconds == 45


def test_missing_config_uses_default_web_search_timeout(tmp_path: Path) -> None:
    assert (
        load_project_config(tmp_path).web_search_timeout_seconds
        == DEFAULT_WEB_SEARCH_TIMEOUT_SECONDS
    )


def test_reads_web_search_output_max_chars_from_config(tmp_path: Path) -> None:
    _write_config(tmp_path, "web_search_output_max_chars = 5000\n")

    assert load_project_config(tmp_path).web_search_output_max_chars == 5000


def test_checkpoint_retention_days_from_config(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "[checkpoint]\nretention_days = 7\nprune_on_start = false\n",
    )

    cfg = load_project_config(tmp_path)
    assert cfg.checkpoint.retention_days == 7
    assert cfg.checkpoint.prune_on_start is False


def test_checkpoint_missing_section_uses_defaults(tmp_path: Path) -> None:
    _write_config(tmp_path, "max_model_calls = 10\n")

    cfg = load_project_config(tmp_path)
    assert cfg.checkpoint.retention_days == 30
    assert cfg.checkpoint.prune_on_start is True


def test_checkpoint_invalid_retention_falls_back(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "[checkpoint]\nretention_days = -1\n",
    )

    assert load_project_config(tmp_path).checkpoint.retention_days == 30


def test_checkpoint_generated_template_round_trips(tmp_path: Path) -> None:
    _write_config(tmp_path, CONFIG_TEMPLATE)

    cfg = load_project_config(tmp_path)
    assert cfg.checkpoint.retention_days == 30
    assert cfg.checkpoint.prune_on_start is True


def test_boolean_rejected_for_integer_field(tmp_path: Path) -> None:
    _write_config(tmp_path, "max_model_calls = true\n")

    assert load_project_config(tmp_path).max_model_calls == DEFAULT_MAX_MODEL_CALLS


def test_invalid_env_allowlist_extra_filtered(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        'env_allowlist_extra = ["VALID_VAR", "bad-var", "123", "lower_case", "ANOTHER"]\n',
    )

    cfg = load_project_config(tmp_path)
    assert cfg.env_allowlist_extra == ("VALID_VAR", "ANOTHER")


def test_sandbox_config_defaults_to_local(tmp_path: Path) -> None:
    config = load_project_config(tmp_path)

    assert config.sandbox.kind == "local"
    assert config.sandbox.image is None
    assert config.sandbox.workspace_mount == "/mnt/workspace"


def test_sandbox_config_reads_docker_table(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        '[sandbox]\nkind = "docker"\nimage = "python:3.12-bookworm"\n',
    )

    config = load_project_config(tmp_path)

    assert config.sandbox.kind == "docker"
    assert config.sandbox.image == "python:3.12-bookworm"
    assert config.sandbox.workspace_mount == "/mnt/workspace"


def test_sandbox_config_rejects_unknown_keys() -> None:
    """`workspace` is not `workspace_mount`. Silently ignoring the typo would
    leave the user believing they configured a mount they did not."""
    with pytest.raises(ValidationError, match="workspace"):
        SandboxConfig(kind="docker", image="python:3.12", workspace="/mnt/elsewhere")  # type: ignore[call-arg]


def test_validate_sandbox_config_rejects_unknown_keys(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        '[sandbox]\nkind = "docker"\nimage = "python:3.12"\nworkspace = "/mnt/workspace"\n',
    )

    with pytest.raises(SandboxConfigError, match="workspace"):
        validate_sandbox_config(tmp_path)


@pytest.mark.parametrize("bad", ["/abs/path", "../escape", "a/../../etc", "", "."])
def test_sandbox_config_rejects_escaping_mask_paths(bad: str) -> None:
    with pytest.raises(ValidationError):
        SandboxConfig(mask_paths=(bad,))


def test_sandbox_config_masks_host_build_dirs_by_default() -> None:
    """Defence, not opt-in: a host .venv in the mount is a loaded gun."""
    assert SandboxConfig().mask_paths == (".venv", "node_modules")


def test_sandbox_config_mask_paths_can_be_disabled(tmp_path: Path) -> None:
    _write_config(tmp_path, "[sandbox]\nmask_paths = []\n")

    assert load_project_config(tmp_path).sandbox.mask_paths == ()


def test_sandbox_config_rejects_mount_outside_mnt() -> None:
    with pytest.raises(ValidationError):
        SandboxConfig(kind="local", workspace_mount="/workspace")


@pytest.mark.parametrize(
    "rejected_mount",
    [
        "/workspace",
        "/mntfoo",
        "/mntish/x",
        "/mnt/../etc",
        "/mnt/../../root",
        "mnt/workspace",
    ],
)
def test_sandbox_config_rejects_invalid_mounts(rejected_mount: str) -> None:
    with pytest.raises(ValidationError):
        SandboxConfig(kind="local", workspace_mount=rejected_mount)


@pytest.mark.parametrize(
    "accepted_mount",
    [
        "/mnt",
        "/mnt/workspace",
        "/mnt/deep/nested",
    ],
)
def test_sandbox_config_accepts_valid_mounts(accepted_mount: str) -> None:
    config = SandboxConfig(kind="local", workspace_mount=accepted_mount)
    assert config.workspace_mount == accepted_mount


def test_sandbox_config_requires_image_for_docker() -> None:
    with pytest.raises(ValidationError):
        SandboxConfig(kind="docker")


def test_validate_sandbox_config_raises_on_docker_without_image(tmp_path: Path) -> None:
    _write_config(tmp_path, '[sandbox]\nkind = "docker"\n')

    with pytest.raises(SandboxConfigError, match="image"):
        validate_sandbox_config(tmp_path)


def test_validate_sandbox_config_raises_on_bad_mount(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        '[sandbox]\nkind = "docker"\nimage = "python:3.12"\nworkspace_mount = "/workspace"\n',
    )

    with pytest.raises(SandboxConfigError, match="/mnt"):
        validate_sandbox_config(tmp_path)


def test_validate_sandbox_config_error_is_compact(tmp_path: Path) -> None:
    """The message drops pydantic's ValidationError dump, keeping only the gist.

    No "N validation error(s) for SandboxConfig" banner, no indented "Value
    error," prefix, and no "https://errors.pydantic.dev/..." link — just the
    "invalid [sandbox] in config.toml: <reason>" a user can act on.
    """
    _write_config(tmp_path, '[sandbox]\nkind = "docker"\n')

    with pytest.raises(SandboxConfigError) as excinfo:
        validate_sandbox_config(tmp_path)

    message = str(excinfo.value)
    assert "invalid [sandbox] in config.toml" in message
    assert "image is required" in message
    assert "errors.pydantic.dev" not in message
    assert "1 validation error" not in message


def test_validate_sandbox_config_passes_when_absent(tmp_path: Path) -> None:
    validate_sandbox_config(tmp_path)  # no [sandbox] table: nothing to validate


def test_load_project_config_stays_lenient_on_bad_sandbox_table(tmp_path: Path) -> None:
    """The hot path never raises; validate_sandbox_config() is the loud gate."""
    _write_config(tmp_path, '[sandbox]\nkind = "docker"\n')

    config = load_project_config(tmp_path)

    assert config.sandbox.kind == "local"
