from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable
from pathlib import Path
from types import FrameType, TracebackType
from typing import cast

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.defer import DeferStack
from milky_frog.domain import ResumeError, RunCancellation, RunRequest, RunResult
from milky_frog.handlers import BaseHandler, EventDispatcher, default_handlers
from milky_frog.harness.runner import Harness
from milky_frog.harness.tools import ToolRegistry, default_tools
from milky_frog.harness.tools.tool_policy import ToolPolicy
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
        handlers: EventDispatcher | None = None,
        bundles: list[BaseHandler] | None = None,
        tool_policy: ToolPolicy | None = None,
    ) -> None:
        api_key, model = self.require_model_configuration(settings)
        self._checkpoints = SqliteCheckpointStore(settings.database_path)

        self._model_name = model
        self._dispatcher = handlers if handlers is not None else EventDispatcher()

        # Assemble every lifecycle handler bundle in one place (checkpointing,
        # tool policy, Skills, caller-supplied bundles, Langfuse), then register
        # each on the dispatcher and track it so close() releases every one uniformly.
        self._handlers: list[BaseHandler] = default_handlers(
            settings,
            self._checkpoints,
            tool_policy=tool_policy,
            extra=bundles or (),
        )
        for bundle in self._handlers:
            bundle.register(self._dispatcher)

        self._model = OpenAIModel(api_key=api_key, model=model, base_url=settings.base_url)
        self._harness = Harness(
            model=self._model,
            tools=ToolRegistry(default_tools()),
            checkpoints=self._checkpoints,
            handlers=self._dispatcher,
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cancellation: RunCancellation | None = None

    @staticmethod
    def require_model_configuration(settings: Settings) -> tuple[str, str]:
        api_key = settings.api_key
        model = settings.model
        if not api_key or not model:
            raise MissingModelConfiguration("model configuration is missing")
        return api_key, model

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        handlers: EventDispatcher | None = None,
        bundles: list[BaseHandler] | None = None,
        tool_policy: ToolPolicy | None = None,
    ) -> MilkyFrog:
        return cls(settings, handlers, bundles, tool_policy)

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
        """Release assembled Handlers, the model client, and the reused loop, once."""
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
        loop = self._loop
        defer = (
            DeferStack(logger=logger)
            .defer_set(self, "_handlers", [])
            .defer_set(self, "_loop", None)
            .defer_close(loop, label="event_loop")
            .defer_shutdown_asyncgens(loop)
            .defer_aclose(self._model, label="OpenAIModel")
        )
        for handler in self._handlers:
            defer.defer_aclose(handler)
        defer.run_sync(loop)

    @property
    def model_name(self) -> str:
        """The configured model identifier (for display in the TUI)."""
        return self._model_name

    @property
    def dispatcher(self) -> EventDispatcher:
        """The shared EventDispatcher — presentation bundles register here via ``bundles=``."""
        return self._dispatcher

    @property
    def checkpoints(self) -> SqliteCheckpointStore:
        """The shared checkpoint store — TUI resolves run IDs here."""
        return self._checkpoints

    @property
    def harness(self) -> Harness:
        """The async Harness — TUI uses this to drive Runs inside Textual's
        event loop instead of going through the sync ``run()`` / ``resume()``
        wrappers that create their own loop."""
        return self._harness

    def cancel(self) -> None:
        """Request cooperative cancellation of the foreground Run."""
        if self._cancellation is not None:
            self._cancellation.cancel()

    def run(self, prompt: str, workspace: Path) -> RunResult:
        """Start one goal synchronously."""
        config = load_project_config(workspace)
        self._cancellation = RunCancellation()
        return self._drive(
            self._harness.run(
                RunRequest(
                    prompt,
                    workspace,
                    max_model_calls=config.max_model_calls,
                    cancellation=self._cancellation,
                )
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
        self._cancellation = RunCancellation()
        return self._drive(
            self._harness.resume(
                run_id,
                max_model_calls=config.max_model_calls,
                cancellation=self._cancellation,
                prompt=prompt,
            )
        )

    def _drive(self, coro: Awaitable[RunResult]) -> RunResult:
        """Run one foreground coroutine on the reused loop with SIGINT→cancel wiring."""
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
        loop = self._loop
        previous_sigint = signal.getsignal(signal.SIGINT)

        def _request_cancel(signum: int, frame: FrameType | None) -> None:
            if self._cancellation is not None and not self._cancellation.is_cancelled:
                self._cancellation.cancel()
                return
            if callable(previous_sigint):
                previous_sigint(signum, frame)
            elif previous_sigint is signal.SIG_DFL:
                signal.default_int_handler(signum, frame)

        defer = (
            DeferStack()
            .defer_yield_loop(loop)
            .defer_set(self, "_cancellation", None)
            .defer_signal(
                signal.SIGINT,
                cast(signal.Handlers, previous_sigint),
                label="restore_sigint",
            )
        )

        signal.signal(signal.SIGINT, _request_cancel)
        try:
            return loop.run_until_complete(coro)
        finally:
            defer.run()
