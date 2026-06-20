from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from types import FrameType, TracebackType

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.domain import RunCancellation, RunRequest, RunResult
from milky_frog.handlers import BaseHandler, HandlerRegistry
from milky_frog.harness import Harness
from milky_frog.harness.tools import ToolRegistry
from milky_frog.models import OpenAIModel
from milky_frog.project import load_project_config
from milky_frog.settings import Settings


class MissingModelConfiguration(ValueError):
    pass


class MilkyFrog:
    """Runs configured Milky Frog goals while hiding runtime assembly.

    Owns the reused event loop and the lifetime of the assembled infrastructure
    Handlers. Use as a context manager so their resources (and the loop) are
    released once, around a whole session:

        with MilkyFrog.from_settings(settings) as frog:
            frog.run(prompt, workspace)
    """

    def __init__(
        self,
        settings: Settings,
        handlers: HandlerRegistry | None = None,
        bundles: list[BaseHandler] | None = None,
    ) -> None:
        api_key = settings.api_key
        model = settings.model
        if not api_key or not model:
            raise MissingModelConfiguration("model configuration is missing")
        # Handler composition is the caller's (the HandlerFactory's) job; the
        # runtime only owns the bundles' resource lifetime via ``aclose``.
        self._handlers: list[BaseHandler] = list(bundles) if bundles else []
        self._harness = Harness(
            model=OpenAIModel(api_key=api_key, model=model, base_url=settings.base_url),
            tools=ToolRegistry(),
            checkpoints=SqliteCheckpointStore(settings.database_path),
            handlers=handlers if handlers is not None else HandlerRegistry(),
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cancellation: RunCancellation | None = None

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        handlers: HandlerRegistry | None = None,
        bundles: list[BaseHandler] | None = None,
    ) -> MilkyFrog:
        return cls(settings, handlers, bundles)

    def __enter__(self) -> MilkyFrog:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Release assembled Handlers and the reused event loop, once."""
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
        try:
            for handler in self._handlers:
                self._loop.run_until_complete(handler.aclose())
        finally:
            self._handlers = []
            self._loop.close()

    def cancel(self) -> None:
        """Request cooperative cancellation of the foreground Run."""
        if self._cancellation is not None:
            self._cancellation.cancel()

    def run(self, prompt: str, workspace: Path) -> RunResult:
        """Run one goal synchronously.

        Successive calls reuse a single event loop (and the model's connection
        pool), so this must not be called while another event loop is running.
        """
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
        config = load_project_config(workspace)
        self._cancellation = RunCancellation()
        request = RunRequest(
            prompt,
            workspace,
            max_model_calls=config.max_model_calls,
            cancellation=self._cancellation,
        )
        previous_sigint = signal.getsignal(signal.SIGINT)

        def _request_cancel(signum: int, frame: FrameType | None) -> None:
            # First Ctrl+C requests cooperative cancel. A second Ctrl+C restores
            # the previous handler and may force-abort the foreground Run.
            if self._cancellation is not None and not self._cancellation.is_cancelled:
                self._cancellation.cancel()
                return
            if callable(previous_sigint):
                previous_sigint(signum, frame)
            elif previous_sigint is signal.SIG_DFL:
                signal.default_int_handler(signum, frame)

        signal.signal(signal.SIGINT, _request_cancel)
        try:
            result = self._loop.run_until_complete(self._harness.run(request))
        finally:
            signal.signal(signal.SIGINT, previous_sigint)
            self._cancellation = None
            # Drain async-generator cleanup tasks (athrow GeneratorExit) that
            # the OpenAI stream schedules after the run completes. Without this
            # the reused loop leaves them pending and Python prints a warning.
            self._loop.run_until_complete(asyncio.sleep(0))
        return result
