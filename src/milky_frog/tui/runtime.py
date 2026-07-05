from __future__ import annotations

import asyncio
import signal
from types import FrameType
from typing import Any

from textual.message import Message

from milky_frog.app.session import AgentSession
from milky_frog.core.controller import RunController
from milky_frog.settings import Settings
from milky_frog.tui.app import MilkyFrogApp, TuiLaunch
from milky_frog.tui.bundles import make_tui_presentation_handlers
from milky_frog.tui.textual_patch import patch_textual_utf8_decode


class _TuiMessageSink:
    """Late-bind lifecycle Handler output to the Textual app."""

    def __init__(self) -> None:
        self._app: MilkyFrogApp | None = None

    def attach(self, app: MilkyFrogApp | None) -> None:
        self._app = app

    def emit(self, message: Message) -> object:
        app = self._app
        if app is None:
            return False
        return app.post_message(message)


class TuiRuntime:
    """Own the process-level lifecycle for the interactive Terminal UI."""

    def __init__(self, settings: Settings, *, launch: TuiLaunch | None = None) -> None:
        self._launch = launch
        self._sink = _TuiMessageSink()
        self._session = AgentSession(
            settings,
            bundles=make_tui_presentation_handlers(self._sink.emit),
            interactive=True,
        )
        self._app: MilkyFrogApp | None = None

    def run(self) -> Any:
        """Run the Terminal UI with one owned ``AgentSession`` lifecycle."""
        patch_textual_utf8_decode()

        previous_sigint_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self._handle_sigint_before_loop)
        try:
            return asyncio.run(self._run_async())
        except KeyboardInterrupt:
            self._request_shutdown()
            return None
        finally:
            signal.signal(signal.SIGINT, previous_sigint_handler)

    async def _run_async(self) -> Any:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, self._handle_sigint)
        try:
            async with self._session:
                app = MilkyFrogApp(
                    self._session,
                    RunController(self._session.checkpoints),
                    launch=self._launch,
                )
                self._app = app
                self._sink.attach(app)
                if self._session.shutdown_requested:
                    return None
                return await app.run_async()
        finally:
            self._sink.attach(None)
            self._app = None
            loop.remove_signal_handler(signal.SIGINT)

    def _handle_sigint_before_loop(self, _signum: int, _frame: FrameType | None) -> None:
        """Handle Ctrl+C before the asyncio loop owns SIGINT."""
        self._handle_sigint()

    def _handle_sigint(self) -> None:
        self._request_shutdown()
        app = self._app
        if app is not None:
            app.exit()

    def _request_shutdown(self) -> None:
        self._session.request_shutdown()


def run_tui(settings: Settings, *, launch: TuiLaunch | None = None) -> Any:
    return TuiRuntime(settings, launch=launch).run()
