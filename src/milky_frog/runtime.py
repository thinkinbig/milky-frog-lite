from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable
from pathlib import Path
from types import FrameType, TracebackType

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.domain import ResumeError, RunCancellation, RunRequest, RunResult
from milky_frog.handlers import BaseHandler, LangfuseHandler, LifecycleBus
from milky_frog.handlers.policy import PolicyHandler
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
        handlers: LifecycleBus | None = None,
        bundles: list[BaseHandler] | None = None,
        tool_policy: ToolPolicy | None = None,
    ) -> None:
        api_key, model = self.require_model_configuration(settings)
        self._handlers: list[BaseHandler] = list(bundles) if bundles else []
        self._checkpoints = SqliteCheckpointStore(settings.database_path)

        self._model_name = model
        self._bus = handlers if handlers is not None else LifecycleBus()

        # Register built-in tool policy on RunBeforeTool.
        PolicyHandler(tool_policy).register(self._bus)

        # Register lifecycle handlers (observability, policy, …) on the bus
        # so they receive lifecycle events alongside the built-in harness.
        for bundle in self._handlers:
            bundle.register(self._bus)

        # Auto-register Langfuse observability if configured.
        if settings.langfuse.active:
            langfuse = LangfuseHandler(settings.langfuse)
            langfuse.register(self._bus)
            self._handlers.append(langfuse)

        self._harness = Harness(
            model=OpenAIModel(api_key=api_key, model=model, base_url=settings.base_url),
            tools=ToolRegistry(default_tools()),
            checkpoints=self._checkpoints,
            handlers=self._bus,
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
        handlers: LifecycleBus | None = None,
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
        """Release assembled Handlers and the reused event loop, once."""
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
        try:
            for handler in self._handlers:
                try:
                    self._loop.run_until_complete(handler.aclose())
                except Exception:
                    logger.exception("Handler aclose failed: %s", type(handler).__name__)
        finally:
            self._handlers = []
            self._loop.close()
            self._loop = None

    @property
    def model_name(self) -> str:
        """The configured model identifier (for display in the TUI)."""
        return self._model_name

    @property
    def bus(self) -> LifecycleBus:
        """The shared LifecycleBus — TUI subscribes its renderer here."""
        return self._bus

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
            result = self._loop.run_until_complete(coro)
        finally:
            signal.signal(signal.SIGINT, previous_sigint)
            self._cancellation = None
            self._loop.run_until_complete(asyncio.sleep(0))
        return result
