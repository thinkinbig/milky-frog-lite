from __future__ import annotations

from pathlib import Path

from milky_frog.domain import DEFAULT_MAX_MODEL_CALLS
from milky_frog.project import (
    CONFIG_FILENAME,
    CONFIG_TEMPLATE,
    DEFAULT_BASH_TIMEOUT_SECONDS,
    DEFAULT_WEB_SEARCH_TIMEOUT_SECONDS,
    PROJECT_DIRNAME,
    load_project_config,
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


def test_verification_defaults(tmp_path: Path) -> None:
    cfg = load_project_config(tmp_path)
    assert cfg.verification.after_edit is True
    assert cfg.verification.commands == (
        "uv run ruff check .",
        "uv run pytest -q",
    )


def test_verification_from_config(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "[verification]\n"
        "after_edit = false\n"
        'commands = ["uv run ruff check ."]\n',
    )
    cfg = load_project_config(tmp_path)
    assert cfg.verification.after_edit is False
    assert cfg.verification.commands == ("uv run ruff check .",)


def test_verification_missing_section_uses_defaults(tmp_path: Path) -> None:
    _write_config(tmp_path, "max_model_calls = 10\n")
    cfg = load_project_config(tmp_path)
    assert cfg.verification.after_edit is True


def test_verification_generated_template_round_trips(tmp_path: Path) -> None:
    _write_config(tmp_path, CONFIG_TEMPLATE)
    cfg = load_project_config(tmp_path)
    assert cfg.verification.after_edit is True
    assert cfg.verification.commands == (
        "uv run ruff check .",
        "uv run pytest -q",
    )
