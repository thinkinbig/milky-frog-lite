from __future__ import annotations

import signal
from collections.abc import Callable, Coroutine
from pathlib import Path
from types import FrameType
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from milky_frog.app.session import AgentSession
from milky_frog.core.controller import RunController
from milky_frog.settings import Settings
from milky_frog.ui.app import MilkyFrogApp
from milky_frog.ui.runtime import TuiRuntime


def _settings(tmp_path: Path) -> Settings:
    return Settings(home=tmp_path, api_key="test-key", model="test-model", _env_file=None)


def _app(tmp_path: Path) -> MilkyFrogApp:
    session = AgentSession(_settings(tmp_path), bundles=[], interactive=True)
    controller = cast(RunController, MagicMock())
    return MilkyFrogApp(session, controller)


def test_exit_action_requests_shutdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(tmp_path)
    exited = False

    def fake_exit() -> None:
        nonlocal exited
        exited = True

    monkeypatch.setattr(app, "exit", fake_exit)

    app.action_request_exit()

    assert exited is True
    assert app.session.shutdown_requested is True


def test_runtime_installs_sigint_handler_before_asyncio_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = TuiRuntime(_settings(tmp_path))
    installed_handlers: list[Callable[[int, FrameType | None], None] | int | signal.Handlers] = []

    def fake_signal(
        signum: signal.Signals,
        handler: Callable[[int, FrameType | None], None] | int | signal.Handlers,
    ) -> Callable[[int, FrameType | None], None] | int | signal.Handlers:
        if signum == signal.SIGINT:
            installed_handlers.append(handler)
        return signal.SIG_DFL

    def fake_asyncio_run(coro: Coroutine[Any, Any, Any]) -> None:
        try:
            handler = installed_handlers[0]
            assert callable(handler)
            handler(signal.SIGINT, None)
        finally:
            coro.close()

    monkeypatch.setattr("milky_frog.ui.runtime.signal.signal", fake_signal)
    monkeypatch.setattr("milky_frog.ui.runtime.asyncio.run", fake_asyncio_run)

    runtime.run()

    assert runtime._session.shutdown_requested is True


def test_runtime_swallows_startup_keyboard_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = TuiRuntime(_settings(tmp_path))

    def fake_asyncio_run(coro: Coroutine[Any, Any, Any]) -> None:
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr("milky_frog.ui.runtime.asyncio.run", fake_asyncio_run)

    assert runtime.run() is None
    assert runtime._session.shutdown_requested is True
