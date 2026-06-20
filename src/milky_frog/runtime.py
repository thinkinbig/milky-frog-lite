from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from types import FrameType

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.domain import RunCancellation, RunRequest, RunResult
from milky_frog.handlers import HandlerRegistry, LangfuseHandler
from milky_frog.harness import Harness
from milky_frog.harness.tools import ToolRegistry
from milky_frog.models import OpenAIModel
from milky_frog.project import load_project_config
from milky_frog.settings import Settings


class MissingModelConfiguration(ValueError):
    pass


class MilkyFrog:
    """Runs configured Milky Frog goals while hiding runtime assembly."""

    def __init__(self, settings: Settings, handlers: HandlerRegistry | None = None) -> None:
        api_key = settings.api_key
        model = settings.model
        if not api_key or not model:
            raise MissingModelConfiguration("model configuration is missing")
        if handlers is None:
            handlers = HandlerRegistry()
        self._langfuse: LangfuseHandler | None = None
        if settings.langfuse.active:
            self._langfuse = LangfuseHandler(settings.langfuse)
            self._langfuse.register(handlers)
        self._harness = Harness(
            model=OpenAIModel(api_key=api_key, model=model, base_url=settings.base_url),
            tools=ToolRegistry(),
            checkpoints=SqliteCheckpointStore(settings.database_path),
            handlers=handlers,
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cancellation: RunCancellation | None = None

    @classmethod
    def from_settings(
        cls, settings: Settings, handlers: HandlerRegistry | None = None
    ) -> MilkyFrog:
        return cls(settings, handlers)

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
        except Exception:
            if self._langfuse:
                self._langfuse.flush()
            raise
        finally:
            signal.signal(signal.SIGINT, previous_sigint)
            self._cancellation = None
            # Drain async-generator cleanup tasks (athrow GeneratorExit) that
            # the OpenAI stream schedules after the run completes. Without this
            # the reused loop leaves them pending and Python prints a warning.
            self._loop.run_until_complete(asyncio.sleep(0))
        if self._langfuse:
            self._langfuse.finalize(result.run_id)
        return result
