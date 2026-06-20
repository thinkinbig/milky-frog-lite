from __future__ import annotations

from pathlib import Path

import pytest

from milky_frog.settings import Settings


def test_reads_configuration_from_dotenv_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text(
        "MILKY_FROG_API_KEY=from-dotenv\n"
        "export MILKY_FROG_MODEL='deepseek-v4-flash'\n"
        "MILKY_FROG_BASE_URL=\"https://api.deepseek.com\"\n"
        "# a comment\n"
        "\n",
        encoding="utf-8",
    )
    for key in ("MILKY_FROG_API_KEY", "MILKY_FROG_MODEL", "MILKY_FROG_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)

    settings = Settings.from_environment()

    assert settings.api_key == "from-dotenv"
    assert settings.model == "deepseek-v4-flash"
    assert settings.base_url == "https://api.deepseek.com"


def test_real_environment_overrides_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text("MILKY_FROG_API_KEY=from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("MILKY_FROG_API_KEY", "from-environment")
    monkeypatch.chdir(tmp_path)

    settings = Settings.from_environment()

    assert settings.api_key == "from-environment"


def test_missing_dotenv_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("MILKY_FROG_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)

    settings = Settings.from_environment()

    assert settings.api_key is None


def test_empty_environment_values_are_treated_as_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text(
        "MILKY_FROG_API_KEY=\nMILKY_FROG_MODEL=\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MILKY_FROG_API_KEY", "")
    monkeypatch.setenv("MILKY_FROG_MODEL", "")
    monkeypatch.chdir(tmp_path)

    settings = Settings.from_environment()

    assert settings.api_key is None
    assert settings.model is None
