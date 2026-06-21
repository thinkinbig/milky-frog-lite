from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path
from types import FrameType, TracebackType

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.domain import RunCancellation, RunResult
from milky_frog.foreground import ForegroundRun, ResumeRun, StartRun
from milky_frog.handlers import BaseHandler, HandlerRegistry
from milky_frog.harness import Harness, ResumeError
from milky_frog.harness.tools import ToolRegistry, default_tools
from milky_frog.infra.steering import NullSteeringProducer, SteeringProducer, steering_channel
from milky_frog.models import OpenAIModel
from milky_frog.project import load_project_config
from milky_frog.settings import Settings

logger = logging.getLogger(__name__)


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
        *,
        steering_producer: SteeringProducer | None = None,
    ) -> None:
        api_key, model = self.require_model_configuration(settings)
        # Handler composition is the caller's (the HandlerFactory's) job; the
        # runtime only owns the bundles' resource lifetime via ``aclose``.
        self._handlers: list[BaseHandler] = list(bundles) if bundles else []
        self._checkpoints = SqliteCheckpointStore(settings.database_path)
        self._harness = Harness(
            model=OpenAIModel(api_key=api_key, model=model, base_url=settings.base_url),
            tools=ToolRegistry(default_tools()),
            checkpoints=self._checkpoints,
            handlers=handlers if handlers is not None else HandlerRegistry(),
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cancellation: RunCancellation | None = None
        self._steering_producer = steering_producer or NullSteeringProducer()

    @staticmethod
    def require_model_configuration(settings: Settings) -> tuple[str, str]:
        """Return (api_key, model), or raise if either is missing.

        Call before composing resource-holding Handlers so a missing
        configuration fails fast without leaking half-built infrastructure.
        """
        api_key = settings.api_key
        model = settings.model
        if not api_key or not model:
            raise MissingModelConfiguration("model configuration is missing")
        return api_key, model

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        handlers: HandlerRegistry | None = None,
        bundles: list[BaseHandler] | None = None,
        *,
        steering_producer: SteeringProducer | None = None,
    ) -> MilkyFrog:
        return cls(settings, handlers, bundles, steering_producer=steering_producer)

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
                # Isolate each bundle: one bundle's aclose failure must not abort
                # releasing the rest, nor mask an exception that is exiting the
                # ``with`` block.
                try:
                    self._loop.run_until_complete(handler.aclose())
                except Exception:
                    logger.exception("Handler aclose failed: %s", type(handler).__name__)
        finally:
            self._handlers = []
            self._loop.close()
            # Reset so a later run() recreates a fresh loop instead of reusing a
            # closed one; close() stays idempotent (no handlers left to release).
            self._loop = None

    def cancel(self) -> None:
        """Request cooperative cancellation of the foreground Run."""
        if self._cancellation is not None:
            self._cancellation.cancel()

    def run(self, prompt: str, workspace: Path) -> RunResult:
        """Start one goal synchronously.

        Successive calls reuse a single event loop (and the model's connection
        pool), so this must not be called while another event loop is running.
        """
        config = load_project_config(workspace)
        return self._drive(
            StartRun(
                self._harness,
                prompt=prompt,
                workspace=workspace,
                max_model_calls=config.max_model_calls,
            )
        )

    def resume(self, run_id: str, prompt: str | None = None) -> RunResult:
        """Advance an existing Run synchronously.

        Without a prompt, picks up pending work (PAUSED_LIMIT / CANCELLED). With
        a prompt, appends a new user turn and advances — continuing any terminal
        Run, including a COMPLETED conversation. Raises ``ResumeError`` if the
        Run is unknown or cannot be advanced as requested.
        """
        try:
            run_id = self._checkpoints.resolve_run_id(run_id)
        except LookupError as error:
            raise ResumeError(f"unknown Run: {run_id}") from error
        except ValueError as error:
            raise ResumeError(f"ambiguous Run prefix: {run_id}") from error
        stored = self._checkpoints.get_run(run_id)
        if stored is None:
            raise ResumeError(f"unknown Run: {run_id}")
        config = load_project_config(stored.workspace)
        return self._drive(
            ResumeRun(
                self._harness,
                run_id=run_id,
                max_model_calls=config.max_model_calls,
                prompt=prompt,
            )
        )

    def _drive(self, foreground: ForegroundRun) -> RunResult:
        """Run one foreground awaitable on the reused loop, wiring SIGINT to a
        cooperative cancel and a session-scoped steering producer for this Run."""
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
        self._cancellation = RunCancellation()
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
            with steering_channel(self._steering_producer) as steering:
                result = self._loop.run_until_complete(foreground(self._cancellation, steering))
        finally:
            signal.signal(signal.SIGINT, previous_sigint)
            self._cancellation = None
            # Drain async-generator cleanup tasks (athrow GeneratorExit) that
            # the OpenAI stream schedules after the run completes. Without this
            # the reused loop leaves them pending and Python prints a warning.
            self._loop.run_until_complete(asyncio.sleep(0))
        return result
