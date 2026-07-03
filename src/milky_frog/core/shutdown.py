from __future__ import annotations

import logging
from collections.abc import Callable
from types import TracebackType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from milky_frog.core.runtime.foreground import ForegroundRun
    from milky_frog.events.hub import Handler
    from milky_frog.models.openai import OpenAIModel

logger = logging.getLogger(__name__)


class ShutdownManager:
    """Orchestrates graceful teardown in two ordered, idempotent phases.

    Phase 1 — ``shutdown_run()``:
        Cooperatively cancels the foreground Run (RunCancellation token +
        seal_interrupt checkpoint), then cancels the optional attached
        asyncio Task (e.g. a Textual worker).  Called from SIGINT handlers
        or UI exit actions.

    Phase 2 — ``cleanup()``:
        Releases lifecycle Handler bundles and the Model HTTP client.
        Called exactly once from ``AgentSession.__aexit__``.

    Both phases are idempotent so overlapping callers (signal handler +
    key binding + async-context exit) converge on the same ordering.
    No caller needs to know what the other callers are doing.
    """

    def __init__(self) -> None:
        self._shutdown_done = False
        self._cleanup_done = False

        # Wired during session setup (AgentSession.__aenter__).
        self._foreground: ForegroundRun | None = None
        self._handlers: list[Handler] = []
        self._model: OpenAIModel | None = None

        # Optional worker-cancel callback (set by MilkyFrogApp).
        self._cancel_worker: Callable[[], None] | None = None

    # ── Wiring ───────────────────────────────────────────────────────

    def wire(
        self,
        foreground: ForegroundRun,
        handlers: list[Handler],
        model: OpenAIModel,
    ) -> None:
        """Bind the runtime resources this manager controls.

        Called once from ``AgentSession.__aenter__`` after ``ForegroundRun``,
        handler list, and model client have been created.
        """
        self._foreground = foreground
        self._handlers = handlers
        self._model = model

    def attach_worker(self, cancel: Callable[[], None] | None) -> None:
        """Register a callable that cancels the foreground asyncio Task.

        Called by ``MilkyFrogApp`` when a new worker is created so the
        manager can cancel it during ``shutdown_run()``.
        """
        self._cancel_worker = cancel

    # ── Phase 1: cancel the Run ──────────────────────────────────────

    def shutdown_run(self) -> None:
        """Cancel the foreground Run and attached worker, at most once."""
        if self._shutdown_done:
            return
        self._shutdown_done = True

        fg = self._foreground
        if fg is not None:
            fg.shutdown()

        cancel = self._cancel_worker
        if cancel is not None:
            cancel()

    # ── Phase 2: release session resources ───────────────────────────

    async def cleanup(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release lifecycle Handler bundles and the Model client, once.

        Always runs Phase 1 first (``shutdown_run()``) so the Run is sealed
        before shared resources (HTTP client, DB connections) are torn down.
        """
        if self._cleanup_done:
            return
        self._cleanup_done = True

        # Ensure the Run is sealed before closing shared resources.
        self.shutdown_run()

        for handler in reversed(self._handlers):
            try:
                await handler.__aexit__(exc_type, exc, traceback)
            except Exception:
                logger.exception("Handler cleanup failed: %s", type(handler).__qualname__)

        model = self._model
        if model is not None:
            try:
                await model.__aexit__(exc_type, exc, traceback)
            except Exception:
                logger.exception("Model cleanup failed")
