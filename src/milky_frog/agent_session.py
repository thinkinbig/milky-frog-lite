from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.checkpoint.base import StoredRun
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
from milky_frog.harness.sandbox import LocalSandbox, SandboxFactory
from milky_frog.harness.tools import ToolRegistry, default_tools
from milky_frog.harness.tools.tool_policy import SessionToolPolicy
from milky_frog.models import OpenAIModel
from milky_frog.project import load_project_config
from milky_frog.settings import Settings

logger = logging.getLogger(__name__)


class MissingModelConfiguration(ValueError):
    """Raised when the model API key or model name is not configured."""


@dataclass(frozen=True, slots=True)
class AgentSessionConfig:
    """All session-level policy in one place.

    Passed to ``AgentSession`` at construction time; merged with per-project
    ``.milky-frog/config.toml`` for workspace-specific overrides (e.g.
    ``max_model_calls``).
    """

    max_model_calls: int = DEFAULT_MAX_MODEL_CALLS
    sandbox_factory: SandboxFactory = LocalSandbox


class AgentSession:
    """Central runtime: resources + orchestration. Pure async.

    Owns the full lifecycle of all async resources (model, handlers), the
    session-level ``AgentSessionConfig``, and the orchestration state for the
    current Run.  The caller provides the event loop — TUI uses Textual's
    own loop, CLI uses ``asyncio.run()``.

        async with AgentSession.from_settings(settings, bundles=[...]) as session:
            result = await session.start_new("build feature X")

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
    ) -> None:
        api_key, model = self.require_model_configuration(settings)
        self._config = config or AgentSessionConfig()
        self._checkpoints = SqliteCheckpointStore(settings.database_path)
        self._model_name = model
        self._dispatcher = handlers if handlers is not None else EventDispatcher()
        self._handlers: list[BaseHandler] = default_handlers(
            settings,
            self._checkpoints,
            model_name=model,
            extra=bundles or (),
        )
        for bundle in self._handlers:
            bundle.register(self._dispatcher)
        self._model = OpenAIModel(api_key=api_key, model=model, base_url=settings.base_url)
        self._harness = AgentHarness(
            model=self._model,
            tools=ToolRegistry(default_tools()),
            checkpoints=self._checkpoints,
            handlers=self._dispatcher,
            sandbox_factory=self._config.sandbox_factory,
        )

        # ── Mutable session-level policy ─────────────────────────────
        self.policy = SessionToolPolicy()
        self._dispatcher.set_context(HandlerContext(policy=self.policy))

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
        return self._dispatcher

    @property
    def checkpoints(self) -> SqliteCheckpointStore:
        return self._checkpoints

    @property
    def harness(self) -> AgentHarness:
        return self._harness

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
        await self._model.__aenter__()
        for handler in self._handlers:
            await handler.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        for handler in reversed(self._handlers):
            try:
                await handler.__aexit__(exc_type, exc, traceback)
            except Exception:
                logger.exception("Cleanup failed: %s", type(handler).__qualname__)
        try:
            await self._model.__aexit__(exc_type, exc, traceback)
        except Exception:
            logger.exception("Cleanup failed: model")

    # ── Orchestration (sync) ──────────────────────────────────────────

    def cancel(self) -> None:
        """Request cooperative cancellation of the foreground Run."""
        if self._cancellation is not None:
            self._cancellation.cancel()

    # ── Orchestration (async) ─────────────────────────────────────────

    async def start_new(self, task: str, workspace: Path | None = None) -> RunResult:
        """Start a fresh Run: seed transcript, advance through the Harness."""
        workspace = (workspace or Path.cwd()).resolve(strict=True)
        project_cfg = load_project_config(workspace)
        self.busy = True
        self._cancellation = RunCancellation()
        try:
            result = await self._harness.run(
                RunRequest(
                    task,
                    workspace,
                    max_model_calls=project_cfg.max_model_calls,
                    cancellation=self._cancellation,
                )
            )
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
            result = await self._harness.resume(
                stored.run_id,
                max_model_calls=project_cfg.max_model_calls,
                cancellation=self._cancellation,
                prompt=prompt,
            )
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
            result = await self._harness.respond_approval(
                stored.run_id,
                max_model_calls=project_cfg.max_model_calls,
                cancellation=self._cancellation,
                approval=verdict,
            )
            self.run_id = result.run_id
            return result
        finally:
            self.busy = False
            self._cancellation = None

    def _resolve_stored_run(self, run_id: str) -> StoredRun:
        try:
            resolved = self._checkpoints.resolve_run_id(run_id)
        except LookupError as error:
            raise ResumeError(f"unknown Run: {run_id}") from error
        except ValueError as error:
            raise ResumeError(f"ambiguous Run prefix: {run_id}") from error
        stored = self._checkpoints.get_run(resolved)
        if stored is None:
            raise ResumeError(f"unknown Run: {resolved}")
        return stored

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def cancelled_result(run_id: str | None) -> RunResult:
        """Synthetic ``RunResult`` when a worker is hard-cancelled."""
        return RunResult(run_id or "unknown", RunStatus.CANCELLED, "cancelled", 0)
