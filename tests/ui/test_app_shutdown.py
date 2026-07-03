from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from milky_frog.settings import Settings
from milky_frog.ui.app import MilkyFrogApp


def _settings(tmp_path: Path) -> Settings:
    return Settings(home=tmp_path, api_key="test-key", model="test-model", _env_file=None)


def test_sigint_during_startup_defers_exit(tmp_path: Path) -> None:
    app = MilkyFrogApp(_settings(tmp_path))

    app._handle_sigint()

    assert app.session.shutdown_requested is True


def test_sigint_after_session_ready_exits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = MilkyFrogApp(_settings(tmp_path))
    app._run_controller = MagicMock()
    exited = False

    def fake_exit() -> None:
        nonlocal exited
        exited = True

    monkeypatch.setattr(app, "exit", fake_exit)

    app._handle_sigint()

    assert exited is True
    assert app.session.shutdown_requested is True
