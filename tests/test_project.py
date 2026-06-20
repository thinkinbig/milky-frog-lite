from __future__ import annotations

from pathlib import Path

from milky_frog.domain import DEFAULT_MAX_MODEL_CALLS
from milky_frog.project import (
    CONFIG_FILENAME,
    CONFIG_TEMPLATE,
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
