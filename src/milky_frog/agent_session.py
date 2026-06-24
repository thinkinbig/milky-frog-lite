from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.checkpoint.base import CleanupScope, StoredRun
from milky_frog.domain import (
    DEFAULT_MAX_MODEL_CALLS,
    ApprovalVerdict,
    ResumeError,
    RunCancellation,
    RunRequest,
    RunResult,
    RunStatus,
)
from milky_frog.handlers import BaseHandler, EventDispatcher, default_handlers
from milky_frog.handlers.context import HandlerContext
from milky_frog.harness.agent_harness import AgentHarness
from milky_frog.harness.execution_backend import ExecutionBackendFactory, LocalExecutionBackend
from milky_frog.harness.state import seal
from milky_frog.harness.tools import ToolRegistry, default_tools
from milky_frog.harness.tools.tool_policy import SessionToolPolicy
from milky_frog.models import OpenAIModel
from milky_frog.project import load_project_config
from milky_frog.settings import Settings

logger = logging.getLogger(__name__)

_INACTIVE_MSG = "AgentSession is not active; use `async with session`"


def _active[T](value: T | None) -> T:
    if value is None:
        raise InactiveAgentSession(_INACTIVE_MSG)
    return value


class MissingModelConfiguration(ValueError):
    """Raised when the model API key or model name is not configured."""


class InactiveAgentSession(RuntimeError):
    """Raised when a method needs an entered session."""


@dataclass(frozen=True, slots=True)
class AgentSessionConfig:
    """All session-level policy in one place.

    Passed to ``AgentSession`` at construction time; merged with per-project
    ``.milky-frog/config.toml`` for workspace-specific overrides (e.g.
    ``max_model_calls``).
    """

    max_model_calls: int = DEFAULT_MAX_MODEL_CALLS
    backend_factory: ExecutionBackendFactory = LocalExecutionBackend


class AgentSession:
    """Central runtime: resources + orchestration. Pure async.

    Owns the full lifecycle of all async resources (model, handlers), the
    session-level ``AgentSessionConfig``, and the orchestration state for the
    current Run.  The caller provides the event loop — TUI uses Textual's
    own loop, CLI uses ``asyncio.run()``.

        async with AgentSession.from_settings(settings, bundles=[...]) as session:
            result = await session.start_new("build feature X")

    Construction only stores configuration.  ``__aenter__`` wires and
    acquires every session resource; ``__aexit__`` releases them.

    Orchestration methods (``start_new``, ``continue_with``, ``respond_approval``)
    handle the decision about whether to call ``Harness.run``, ``Harness.resume``,
    or ``Harness.respond_approval``, manage the busy flag, and mint cancellation
    tokens.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        config: AgentSessionConfig | None = None,
        handlers: EventDispatcher | None = None,
        bundles: list[BaseHandler] | None = None,
        interactive: bool = False,
    ) -> None:
        api_key, model = self.require_model_configuration(settings)
        self._settings = settings
        self._config = config or AgentSessionConfig()
        self._api_key = api_key
        self._model_name = model
        self._base_url = settings.base_url
        self._dispatcher_override = handlers
        self._extra_bundles = list(bundles or ())
        self._interactive = interactive

        self._checkpoints: SqliteCheckpointStore | None = None
        self._dispatcher: EventDispatcher | None = None
        self._handlers: list[BaseHandler] = []
        self._model: OpenAIModel | None = None
        self._harness: AgentHarness | None = None
        self._policy: SessionToolPolicy | None = None

        # ── Orchestration state ──────────────────────────────────────
        self.run_id: str | None = None
        self.busy: bool = False
        self.pending_approval: str | None = None
        self._cancellation: RunCancellation | None = None

    # ── Properties ──────────────────────────────────────────────────

    @property
    def config(self) -> AgentSessionConfig:
        """The session-level policy configuration."""
        return self._config

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dispatcher(self) -> EventDispatcher:
        return _active(self._dispatcher)

    @property
    def checkpoints(self) -> SqliteCheckpointStore:
        return _active(self._checkpoints)

    @property
    def policy(self) -> SessionToolPolicy:
        return _active(self._policy)

    @property
    def harness(self) -> AgentHarness:
        return _active(self._harness)

    # ── Construction helpers ──────────────────────────────────────────

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
        *,
        config: AgentSessionConfig | None = None,
        handlers: EventDispatcher | None = None,
        bundles: list[BaseHandler] | None = None,
    ) -> AgentSession:
        return cls(settings, config=config, handlers=handlers, bundles=bundles)

    # ── Async resource lifecycle ─────────────────────────────────────

    async def __aenter__(self) -> AgentSession:
        if self._model is not None:
            return self

        self._checkpoints = SqliteCheckpointStore(self._settings.database_path)

        # Cleanup is scoped to the current Workspace: this startup reads policy
        # from the current Workspace's config, so it must never touch another
        # Workspace's Runs in the shared store.
        workspace = Path.cwd()
        scope = CleanupScope.for_workspace(workspace)

        # ── Recover orphaned Runs before anything else ───────────────
        orphans = self._checkpoints.reap_orphans(scope)
        if orphans:
            logger.info("Recovered %d orphaned Run(s)", orphans)

        # ── Prune stale checkpoints on start ─────────────────────────
        project_cfg = load_project_config(workspace)
        if project_cfg.prune_on_start and project_cfg.checkpoint_retention_days > 0:
            cutoff = datetime.now(UTC) - timedelta(days=project_cfg.checkpoint_retention_days)
            pruned = self._checkpoints.prune(cutoff, scope)
            if pruned:
                logger.info(
                    "Pruned %d stale checkpoint(s) (retention: %d days)",
                    pruned,
                    project_cfg.checkpoint_retention_days,
                )

        self._dispatcher = self._dispatcher_override or EventDispatcher()
        self._handlers = default_handlers(
            self._settings,
            self._checkpoints,
            extra=self._extra_bundles,
        )
        for bundle in self._handlers:
            bundle.register(self._dispatcher)

        self._policy = SessionToolPolicy()
        self._dispatcher.set_context(HandlerContext(policy=self._policy))

        self._model = OpenAIModel(
            api_key=self._api_key,
            model=self._model_name,
            base_url=self._base_url,
        )
        await self._model.__aenter__()

        self._harness = AgentHarness(
            model=self._model,
            tools=ToolRegistry(default_tools()),
            checkpoints=self._checkpoints,
            handlers=self._dispatcher,
            backend_factory=self._config.backend_factory,
        )

        for handler in self._handlers:
            await handler.__aenter__()

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.shutdown_foreground_run()
        if self._model is None:
            return

        for handler in reversed(self._handlers):
            try:
                await handler.__aexit__(exc_type, exc, traceback)
            except Exception:
                logger.exception("Cleanup failed: %s", type(handler).__qualname__)

        try:
            await self._model.__aexit__(exc_type, exc, traceback)
        except Exception:
            logger.exception("Cleanup failed: model")

        self._harness = None
        self._model = None
        self._handlers = []
        self._checkpoints = None
        self._dispatcher = None
        self._policy = None

    # ── Orchestration (sync) ──────────────────────────────────────────

    def cancel(self) -> None:
        """Request cooperative cancellation of the foreground Run."""
        if self._cancellation is not None:
            self._cancellation.cancel()

    def shutdown_foreground_run(self) -> None:
        """Stop and checkpoint an in-flight Run without releasing session resources."""
        self.cancel()
        run_id = self.run_id
        checkpoints = self._checkpoints
        if run_id is None or checkpoints is None:
            return
        stored = checkpoints.get_run(run_id)
        if stored is None or stored.status is not RunStatus.RUNNING:
            return
        state = checkpoints.load_state(run_id)
        sealed, _ = seal(state)
        checkpoints.save_state(
            run_id,
            sealed,
            status=RunStatus.CANCELLED,
            final_message="interrupted",
        )

    # ── Orchestration (async) ─────────────────────────────────────────

    async def start_new(self, task: str, workspace: Path | None = None) -> RunResult:
        """Start a fresh Run: seed transcript, advance through the Harness."""
        workspace = (workspace or Path.cwd()).resolve(strict=True)
        project_cfg = load_project_config(workspace)
        self.busy = True
        self._cancellation = RunCancellation()
        try:
            max_calls = 0 if self._interactive else project_cfg.max_model_calls
            try:
                result = await self.harness.run(
                    RunRequest(
                        task,
                        workspace,
                        max_model_calls=max_calls,
                        cancellation=self._cancellation,
                    )
                )
            except asyncio.CancelledError:
                self.shutdown_foreground_run()
                raise
            self.run_id = result.run_id
            return result
        finally:
            self.busy = False
            self._cancellation = None

    async def continue_with(
        self,
        run_id: str,
        *,
        prompt: str | None = None,
    ) -> RunResult:
        """Advance an existing Run.

        The ``run_id`` may be a prefix — it is resolved against the checkpoint
        store first.

        Without ``prompt``, picks up pending work (PAUSED_LIMIT / CANCELLED).
        With ``prompt``, appends a new user turn.

        Raises ``ResumeError`` if the Run is unknown or cannot be resolved.
        """
        stored = self._resolve_stored_run(run_id)
        project_cfg = load_project_config(stored.workspace)
        self.busy = True
        self._cancellation = RunCancellation()
        self.run_id = stored.run_id
        try:
            max_calls = 0 if self._interactive else project_cfg.max_model_calls
            try:
                result = await self.harness.resume(
                    stored.run_id,
                    max_model_calls=max_calls,
                    cancellation=self._cancellation,
                    prompt=prompt,
                )
            except asyncio.CancelledError:
                self.shutdown_foreground_run()
                raise
            self.run_id = result.run_id
            return result
        finally:
            self.busy = False
            self._cancellation = None

    async def respond_approval(self, run_id: str, verdict: ApprovalVerdict) -> RunResult:
        """Release a Run paused on ``WAITING_FOR_APPROVAL`` with the user's verdict.

        Raises ``ResumeError`` if the Run is unknown, ambiguous, or not awaiting
        approval.
        """
        stored = self._resolve_stored_run(run_id)
        if stored.status is not RunStatus.WAITING_FOR_APPROVAL:
            raise ResumeError(f"Run {stored.run_id} is not waiting for tool approval")
        project_cfg = load_project_config(stored.workspace)
        self.busy = True
        self._cancellation = RunCancellation()
        self.run_id = stored.run_id
        try:
            max_calls = 0 if self._interactive else project_cfg.max_model_calls
            try:
                result = await self.harness.respond_approval(
                    stored.run_id,
                    max_model_calls=max_calls,
                    cancellation=self._cancellation,
                    approval=verdict,
                )
            except asyncio.CancelledError:
                self.shutdown_foreground_run()
                raise
            self.run_id = result.run_id
            return result
        finally:
            self.busy = False
            self._cancellation = None

    def _resolve_stored_run(self, run_id: str) -> StoredRun:
        try:
            resolved = self.checkpoints.resolve_run_id(run_id)
        except LookupError as error:
            raise ResumeError(f"unknown Run: {run_id}") from error
        except ValueError as error:
            raise ResumeError(f"ambiguous Run prefix: {run_id}") from error
        stored = self.checkpoints.get_run(resolved)
        if stored is None:
            raise ResumeError(f"unknown Run: {resolved}")
        return stored

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def cancelled_result(run_id: str | None) -> RunResult:
        """Synthetic ``RunResult`` when a worker is hard-cancelled."""
        return RunResult(run_id or "unknown", RunStatus.CANCELLED, "cancelled", 0)
