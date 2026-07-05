from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType

from milky_frog.adapters.local import LocalSandbox
from milky_frog.adapters.models import OpenAIModel
from milky_frog.core.runtime.assemble import make_agent_harness, make_session_handlers
from milky_frog.core.runtime.checkpoint import RunCheckpointFacade
from milky_frog.core.runtime.foreground import ForegroundRun
from milky_frog.core.sandbox import SandboxFactory
from milky_frog.core.session_tool_policy import SessionToolPolicy
from milky_frog.core.shutdown import ShutdownManager
from milky_frog.domain import (
    DEFAULT_MAX_MODEL_CALLS,
    ApprovalVerdict,
    RunResult,
)
from milky_frog.events import EventHub, Handler
from milky_frog.harness.compaction import CompactionHandler
from milky_frog.harness.harness import AgentHarness
from milky_frog.harness.prompt import make_context_loader
from milky_frog.harness.skills import SkillCatalog
from milky_frog.project import load_project_config, project_root
from milky_frog.settings import Settings
from milky_frog.tokens import make_token_counter

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
    """Session-level policy passed to ``AgentSession`` at construction time."""

    max_model_calls: int = DEFAULT_MAX_MODEL_CALLS
    sandbox_factory: SandboxFactory = LocalSandbox


class AgentSession:
    """Thin composition root: wires adapters, delegates Run orchestration to ``ForegroundRun``."""

    def __init__(
        self,
        settings: Settings,
        *,
        config: AgentSessionConfig | None = None,
        hub: EventHub | None = None,
        bundles: list[Handler] | None = None,
        interactive: bool = False,
    ) -> None:
        api_key, model = self.require_model_configuration(settings)
        self._settings = settings
        self._config = config or AgentSessionConfig()
        self._api_key = api_key
        self._model_name = model
        self._base_url = settings.base_url
        self._hub_override = hub
        self._extra_bundles: list[Handler] = list(bundles or ())
        self._interactive = interactive

        self._checkpoints: RunCheckpointFacade | None = None
        self._hub: EventHub | None = None
        self._handlers: list[Handler] = []
        self._model: OpenAIModel | None = None
        self._harness: AgentHarness | None = None
        self._foreground: ForegroundRun | None = None
        self._shutdown: ShutdownManager = ShutdownManager()

    @property
    def config(self) -> AgentSessionConfig:
        return self._config

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def skills_home(self) -> Path:
        """User-scope skills directory (``<home>/skills``).

        Public accessor so UI surfaces can build a ``SkillCatalog`` without
        reaching into ``_settings`` directly.
        """
        return self._settings.home / "skills"

    @property
    def hub(self) -> EventHub:
        return _active(self._hub)

    @property
    def checkpoints(self) -> RunCheckpointFacade:
        return _active(self._checkpoints)

    @property
    def policy(self) -> SessionToolPolicy:
        return _active(self._harness).policy

    @property
    def harness(self) -> AgentHarness:
        return _active(self._harness)

    @property
    def run_id(self) -> str | None:
        fg = self._foreground
        return None if fg is None else fg.run_id

    @run_id.setter
    def run_id(self, value: str | None) -> None:
        fg = self._foreground
        if fg is not None:
            fg.run_id = value

    @property
    def busy(self) -> bool:
        fg = self._foreground
        return False if fg is None else fg.busy

    @busy.setter
    def busy(self, value: bool) -> None:
        fg = self._foreground
        if fg is not None:
            fg.busy = value

    @property
    def pending_approval(self) -> str | None:
        fg = self._foreground
        return None if fg is None else fg.pending_approval

    @pending_approval.setter
    def pending_approval(self, value: str | None) -> None:
        fg = self._foreground
        if fg is not None:
            fg.pending_approval = value

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
        hub: EventHub | None = None,
        bundles: list[Handler] | None = None,
    ) -> AgentSession:
        return cls(settings, config=config, hub=hub, bundles=bundles)

    async def __aenter__(self) -> AgentSession:
        if self._model is not None:
            return self

        from milky_frog.adapters.checkpoint import SqliteCheckpointStore

        store = SqliteCheckpointStore(self._settings.database_path)
        self._checkpoints = RunCheckpointFacade(store)

        workspace = Path.cwd()

        project_cfg = load_project_config(workspace)
        if project_cfg.checkpoint.prune_on_start and project_cfg.checkpoint.retention_days > 0:
            cutoff = datetime.now(UTC) - timedelta(days=project_cfg.checkpoint.retention_days)
            pruned = self._checkpoints.prune(cutoff, workspace)
            if pruned:
                logger.info(
                    "Pruned %d stale checkpoint(s) (retention: %d days)",
                    pruned,
                    project_cfg.checkpoint.retention_days,
                )

        self._hub = self._hub_override or EventHub()

        # Model and token counter are built first: the (optional) CompactionHandler
        # needs both at handler-registration time.
        self._model = OpenAIModel(
            api_key=self._api_key,
            model=self._model_name,
            base_url=self._base_url,
        )
        await self._model.__aenter__()
        counter = make_token_counter(
            self._settings.resolved_provider,
            self._model_name,
            cache_dir=self._settings.home / "tokenizers",
        )

        extra: list[Handler] = list(self._extra_bundles)
        if project_cfg.summarization_enabled:
            extra.append(
                CompactionHandler(
                    self._model,
                    counter,
                    trigger_tokens=project_cfg.summarization_trigger_tokens,
                    keep_recent_tokens=project_cfg.summarization_keep_recent_tokens,
                )
            )
        self._handlers = make_session_handlers(self._settings, store, extra=extra)
        for bundle in self._handlers:
            bundle.register(self._hub)

        self._harness = make_agent_harness(
            model=self._model,
            checkpoints=store,
            hub=self._hub,
            sandbox_factory=self._config.sandbox_factory,
            context_loader=make_context_loader(self._settings.home),
            token_counter=counter,
            max_retries=self._settings.max_retries,
            retry_base_delay=self._settings.retry_base_delay,
            jina_api_key=self._settings.jina_api_key,
        )

        self._foreground = ForegroundRun(
            self._harness,
            self._checkpoints,
            interactive=self._interactive,
        )

        self._shutdown.wire(self._foreground, self._handlers, self._model)

        for handler in self._handlers:
            await handler.__aenter__()

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._shutdown.cleanup(exc_type, exc, traceback)

        self._foreground = None
        self._harness = None
        self._model = None
        self._handlers = []
        self._checkpoints = None
        self._hub = None

    def cancel(self) -> None:
        fg = self._foreground
        if fg is not None:
            fg.cancel()

    def shutdown_foreground_run(self) -> None:
        self._shutdown.shutdown_run()

    def request_shutdown(self) -> None:
        self._shutdown.request_shutdown()

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown.requested

    def attach_worker(self, cancel: Callable[[], None] | None) -> None:
        """Register a callable that cancels the foreground asyncio Task.

        Called by ``MilkyFrogApp`` each time a new Textual worker is created
        so the ``ShutdownManager`` can cancel it during shutdown.
        """
        self._shutdown.attach_worker(cancel)

    async def start_new(
        self,
        task: str,
        workspace: Path | None = None,
        *,
        selected_skills: tuple[str, ...] = (),
    ) -> RunResult:
        skill_content = self._skill_injection(selected_skills, workspace)
        return await _active(self._foreground).start_new(
            task, workspace, skill_content=skill_content
        )

    async def continue_with(
        self,
        run_id: str,
        *,
        prompt: str | None = None,
        selected_skills: tuple[str, ...] | None = None,
    ) -> RunResult:
        """Advance an existing Run.

        ``selected_skills is None`` leaves the Run's activated Skills untouched
        (skills survive resume). A tuple — including ``()`` — re-applies the
        current selection over the persisted value, so mid-run activation and
        ``/skill off`` both take effect on the next turn.
        """
        run_extra: tuple[str, ...] | None = None
        if selected_skills is not None:
            content = self._skill_injection(selected_skills, None)
            run_extra = (content,) if content is not None else ()
        return await _active(self._foreground).continue_with(
            run_id, prompt=prompt, run_extra=run_extra
        )

    def _skill_injection(
        self, selected_skills: tuple[str, ...], workspace: Path | None
    ) -> str | None:
        """Build the eager system-prompt injection for the named Skills, if any."""
        if not selected_skills:
            return None
        resolved = (workspace or Path.cwd()).resolve()
        catalog = SkillCatalog(
            self._settings.home / "skills",
            project_root(resolved) / "skills",
        )
        return _format_skill_injection(catalog, selected_skills)

    async def respond_approval(self, run_id: str, verdict: ApprovalVerdict) -> RunResult:
        return await _active(self._foreground).respond_approval(run_id, verdict)

    @staticmethod
    def cancelled_result(run_id: str | None) -> RunResult:
        return ForegroundRun.cancelled_result(run_id)


def _format_skill_injection(catalog: SkillCatalog, names: tuple[str, ...]) -> str | None:
    """Load named skills and format them for eager system-prompt injection."""
    parts: list[str] = []
    for name in names:
        try:
            skill = catalog.load(name)
        except KeyError:
            logger.warning("selected skill %r not found; skipping", name)
            continue
        parts.append(f'<active_skill name="{name}">\n{skill.instructions.strip()}\n</active_skill>')
    if not parts:
        return None
    intro = (
        "The following skill has been activated for this Run."
        if len(parts) == 1
        else "The following skills have been activated for this Run."
    )
    return intro + " Follow these instructions throughout the task.\n\n" + "\n\n".join(parts)
