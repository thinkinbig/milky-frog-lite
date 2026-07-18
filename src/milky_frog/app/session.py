from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Literal
from uuid import uuid4

from milky_frog.adapters.local import LocalSandbox
from milky_frog.adapters.models import OpenAIModel
from milky_frog.core.runtime.assemble import (
    make_agent_harness,
    make_sandbox_factory,
    make_session_handlers,
)
from milky_frog.core.runtime.checkpoint import RunCheckpointFacade
from milky_frog.core.runtime.foreground import ForegroundRun
from milky_frog.core.sandbox import Sandbox, SandboxFactory
from milky_frog.core.session_tool_policy import SessionToolPolicy
from milky_frog.core.shutdown import ShutdownManager
from milky_frog.domain import (
    DEFAULT_MAX_MODEL_CALLS,
    ApprovalVerdict,
    RunCancellation,
    RunRequest,
    RunResult,
)
from milky_frog.events import EventHub, Handler
from milky_frog.harness.compaction import CompactionHandler
from milky_frog.harness.harness import AgentHarness
from milky_frog.harness.mcp import McpClientManager, load_mcp_config
from milky_frog.harness.prompt import make_context_loader
from milky_frog.harness.skills import SkillCatalog
from milky_frog.harness.subagent_worktree import (
    SubagentWorktreeError,
    create_worktree,
    finalize_worktree,
    git_docker_mounts,
)
from milky_frog.harness.tools import ToolRegistry
from milky_frog.harness.tools.builtins import (
    SubagentOutcome,
    SubagentRejected,
    SubagentTool,
    default_tools,
    read_only_tools,
    write_subagent_tools,
)
from milky_frog.project import load_project_config, project_root
from milky_frog.settings import Settings
from milky_frog.tokens import TokenCounter, make_token_counter

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
    sandbox_factory: SandboxFactory | None = None


@dataclass(frozen=True, slots=True)
class _WorkspaceSandboxFactory:
    """Select the project Sandbox configuration for the Run's Workspace."""

    def __call__(self, workspace: Path) -> Sandbox:
        factory = make_sandbox_factory(load_project_config(workspace))
        return factory(workspace)


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
        self._counter: TokenCounter | None = None
        self._mcp_manager: McpClientManager | None = None
        self._registry: ToolRegistry | None = None
        self._workspace: Path | None = None
        self._nested_harness: AgentHarness | None = None
        self._mcp_reload_lock: asyncio.Lock = asyncio.Lock()

    @property
    def config(self) -> AgentSessionConfig:
        return self._config

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def home(self) -> Path:
        """User-scope state directory (``MILKY_FROG_HOME``, default ``~/.milky-frog``)."""
        return self._settings.home

    @property
    def skills_home(self) -> Path:
        """User-scope skills directory (``<home>/skills``)."""
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
        self._counter = counter

        mcp_manager = McpClientManager()
        await mcp_manager.__aenter__()
        self._mcp_manager = mcp_manager
        try:
            sandbox_factory = self._config.sandbox_factory or _WorkspaceSandboxFactory()

            async def _run_subagent(
                prompt: str,
                capability: Literal["read_only", "write"],
                max_model_calls: int | None,
                cancellation: RunCancellation | None,
                workspace: Path,
                parent_run_id: str,
            ) -> SubagentOutcome:
                calls = DEFAULT_MAX_MODEL_CALLS if max_model_calls is None else max_model_calls
                if capability == "read_only":
                    result = await nested_harness.run(
                        RunRequest(
                            prompt=prompt,
                            workspace=workspace,
                            max_model_calls=calls,
                            cancellation=cancellation,
                            run_kind="subagent",
                            parent_run_id=parent_run_id,
                        )
                    )
                    return SubagentOutcome(result)

                parent_config = load_project_config(workspace)
                if parent_config.sandbox.kind != "docker":
                    raise SubagentRejected(
                        'subagent write capability requires [sandbox].kind = "docker"'
                    )
                management_sandbox = LocalSandbox(workspace, parent_config)
                worktree = await create_worktree(
                    management_sandbox,
                    workspace,
                    uuid4().hex,
                )
                # Not make_sandbox_factory(parent_config): a linked worktree's
                # .git points outside the worktree directory, so the container
                # needs extra bind mounts for git to work at all — see
                # git_docker_mounts.
                from milky_frog.adapters.docker import DockerSandboxFactory

                image = parent_config.sandbox.image
                if image is None:
                    # Same class of misconfiguration as the kind check above, so
                    # it gets the same answer the model can act on. An assert
                    # would surface as a bare AssertionError, and `python -O`
                    # would strip it and let image=None reach `docker run`.
                    raise SubagentRejected(
                        "subagent write capability requires [sandbox].image when "
                        '[sandbox].kind = "docker"'
                    )
                write_sandbox_factory = DockerSandboxFactory(
                    image=image,
                    workspace_mount=parent_config.sandbox.workspace_mount,
                    mask_paths=parent_config.sandbox.mask_paths,
                    config=parent_config,
                    extra_mounts=git_docker_mounts(worktree),
                )
                nested_write_harness = make_agent_harness(
                    model=_active(self._model),
                    checkpoints=store,
                    hub=_active(self._hub),
                    tools=ToolRegistry(
                        write_subagent_tools(jina_api_key=self._settings.jina_api_key)
                    ),
                    sandbox_factory=write_sandbox_factory,
                    context_loader=make_context_loader(self._settings.home),
                    token_counter=counter,
                    max_retries=self._settings.max_retries,
                    retry_base_delay=self._settings.retry_base_delay,
                )
                nested_write_harness.policy.auto_approve()
                try:
                    try:
                        result = await nested_write_harness.run(
                            RunRequest(
                                prompt=prompt,
                                workspace=worktree.path,
                                max_model_calls=calls,
                                cancellation=cancellation,
                                run_kind="subagent",
                                parent_run_id=parent_run_id,
                            )
                        )
                    finally:
                        await write_sandbox_factory.aclose()
                except BaseException:
                    # The nested Run raised (model error, cancellation, container
                    # failure) and nothing downstream holds a reference to the
                    # worktree any more, so `git worktree list` would accumulate a
                    # stale entry per failure. finalize_worktree rather than an
                    # unconditional delete: a Run that failed *after* writing
                    # something keeps that work committed on its branch — this
                    # module never destroys unreviewed work — while the common case
                    # of failing before any write leaves nothing and is removed.
                    with contextlib.suppress(SubagentWorktreeError):
                        await finalize_worktree(management_sandbox, worktree)
                    raise
                worktree_outcome = await finalize_worktree(management_sandbox, worktree)
                return SubagentOutcome(
                    result,
                    worktree=worktree.path,
                    branch=worktree.branch,
                    worktree_kept=worktree_outcome.kept,
                )

            # SubagentTool must be part of the constructor tuple (not added via
            # a later .register() call) so ToolRegistry treats it as a builtin —
            # otherwise a later reload_mcp()/replace_mcp_tools() would drop it.
            registry = ToolRegistry(
                (
                    *default_tools(jina_api_key=self._settings.jina_api_key),
                    SubagentTool(_run_subagent),
                )
            )
            self._registry = registry

            self._handlers = make_session_handlers(
                self._settings,
                store,
                extra=self._extra_bundles,
            )
            for bundle in self._handlers:
                bundle.register(self._hub)

            self._harness = make_agent_harness(
                model=self._model,
                checkpoints=store,
                hub=self._hub,
                tools=registry,
                sandbox_factory=sandbox_factory,
                context_loader=make_context_loader(self._settings.home),
                token_counter=counter,
                max_retries=self._settings.max_retries,
                retry_base_delay=self._settings.retry_base_delay,
            )

            # Shares the session hub: handlers key their work by run_id, while
            # presentation handlers filter nested Runs themselves.
            nested_registry = ToolRegistry(
                read_only_tools(jina_api_key=self._settings.jina_api_key)
            )
            nested_harness = make_agent_harness(
                model=self._model,
                checkpoints=store,
                hub=self._hub,
                tools=nested_registry,
                sandbox_factory=sandbox_factory,
                context_loader=make_context_loader(self._settings.home),
                token_counter=counter,
                max_retries=self._settings.max_retries,
                retry_base_delay=self._settings.retry_base_delay,
            )
            nested_harness.policy.auto_approve()
            self._nested_harness = nested_harness

            self._foreground = ForegroundRun(
                self._harness,
                self._checkpoints,
                interactive=self._interactive,
            )

            self._shutdown.wire(
                self._foreground,
                self._handlers,
                self._model,
                sandbox_factory=sandbox_factory,
            )

            for handler in self._handlers:
                await handler.__aenter__()
        except Exception:
            # `async with self._session:` never calls __aexit__ if __aenter__
            # raises, so connected MCP server subprocesses must be closed here
            # explicitly or they'd leak for the life of the process.
            await mcp_manager.__aexit__(None, None, None)
            self._mcp_manager = None
            raise

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._shutdown.cleanup(exc_type, exc, traceback)

        mcp = self._mcp_manager
        if mcp is not None:
            try:
                await mcp.__aexit__(exc_type, exc, traceback)
            except Exception:
                logger.exception("MCP client cleanup failed")
            self._mcp_manager = None

        self._foreground = None
        self._harness = None
        self._model = None
        self._counter = None
        self._handlers = []
        self._checkpoints = None
        self._hub = None
        self._registry = None
        self._workspace = None
        self._nested_harness = None

    async def reload_mcp(self) -> int:
        """Diff the new config against running servers and connect/disconnect only what changed.

        Reads ``~/.milky-frog/mcp.json`` plus the optional project-level
        ``<workspace>/.milky-frog/mcp.json``, disconnects servers that are no longer
        enabled, connects newly enabled ones, and replaces MCP tools in the shared
        ``ToolRegistry`` in place.

        Returns the number of MCP tools now active.
        """
        workspace = self._workspace or Path.cwd().resolve(strict=True)
        await self._configure_workspace(workspace)
        manager = _active(self._mcp_manager)
        registry = _active(self._registry)

        async with self._mcp_reload_lock:
            new_cfg = load_mcp_config(self._settings.home, workspace)
            new_enabled = {name for name, srv in new_cfg.mcpServers.items() if srv.enabled}
            currently_running = manager.running_servers

            for name in currently_running - new_enabled:
                await manager.disconnect_server(name)
                logger.info("disconnected MCP server %r", name)

            to_connect = {
                name: new_cfg.mcpServers[name] for name in new_enabled - currently_running
            }
            await manager.connect_many(to_connect)

            return registry.replace_mcp_tools(tuple(manager.tools))

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
        resolved_workspace = (workspace or Path.cwd()).resolve(strict=True)
        await self._configure_workspace(resolved_workspace)
        skill_content, injected_skills = self._skill_injection(selected_skills, workspace)
        return await _active(self._foreground).start_new(
            task, resolved_workspace, skill_content=skill_content, selected_skills=injected_skills
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
        await self._configure_workspace(self._stored_workspace(run_id))
        run_extra: tuple[str, ...] | None = None
        injected_skills = selected_skills
        if selected_skills is not None:
            content, injected_skills = self._skill_injection(selected_skills, None)
            run_extra = (content,) if content is not None else ()
        return await _active(self._foreground).continue_with(
            run_id, prompt=prompt, run_extra=run_extra, selected_skills=injected_skills
        )

    async def _configure_workspace(self, workspace: Path) -> None:
        """Bind per-project resources to the Workspace of the first foreground Run."""
        workspace = workspace.resolve(strict=True)
        if self._workspace is not None:
            if self._workspace != workspace:
                raise ValueError(
                    "AgentSession is already bound to a different Workspace; "
                    "create a new AgentSession for that Run"
                )
            return

        project_cfg = load_project_config(workspace)
        checkpoints = _active(self._checkpoints)
        if project_cfg.checkpoint.prune_on_start and project_cfg.checkpoint.retention_days > 0:
            cutoff = datetime.now(UTC) - timedelta(days=project_cfg.checkpoint.retention_days)
            pruned = checkpoints.prune(cutoff, workspace)
            if pruned:
                logger.info(
                    "Pruned %d stale checkpoint(s) (retention: %d days)",
                    pruned,
                    project_cfg.checkpoint.retention_days,
                )

        manager = _active(self._mcp_manager)
        mcp_cfg = load_mcp_config(self._settings.home, workspace)
        enabled_servers = {
            name: server for name, server in mcp_cfg.mcpServers.items() if server.enabled
        }
        await manager.connect_many(enabled_servers)
        _active(self._registry).replace_mcp_tools(tuple(manager.tools))

        if project_cfg.summarization_enabled:
            handler = CompactionHandler(
                _active(self._model),
                _active(self._counter),
                trigger_tokens=project_cfg.summarization_trigger_tokens,
                keep_recent_tokens=project_cfg.summarization_keep_recent_tokens,
            )
            handler.register(_active(self._hub))
            await handler.__aenter__()
            self._handlers.append(handler)
        self._workspace = workspace

    def _stored_workspace(self, run_id: str) -> Path:
        checkpoints = _active(self._checkpoints)
        try:
            resolved_run_id = checkpoints.resolve_run_id(run_id)
        except LookupError as error:
            from milky_frog.domain import ResumeError

            raise ResumeError(f"unknown Run: {run_id}") from error
        except ValueError as error:
            from milky_frog.domain import ResumeError

            raise ResumeError(f"ambiguous Run prefix: {run_id}") from error
        stored = checkpoints.get_run(resolved_run_id)
        if stored is None:
            from milky_frog.domain import ResumeError

            raise ResumeError(f"unknown Run: {resolved_run_id}")
        return stored.workspace

    def _skill_injection(
        self, selected_skills: tuple[str, ...], workspace: Path | None
    ) -> tuple[str | None, tuple[str, ...]]:
        """Build the eager system-prompt injection for the named Skills, if any.

        Returns the injection text (``None`` when nothing resolved) together with
        the names that actually loaded, so the recorded ``selected_skills`` never
        claims a Skill that was not injected.
        """
        if not selected_skills:
            return None, ()
        resolved = (workspace or Path.cwd()).resolve()
        catalog = SkillCatalog(
            self._settings.home / "skills",
            project_root(resolved) / "skills",
        )
        return _format_skill_injection(catalog, selected_skills)

    async def respond_approval(self, run_id: str, verdict: ApprovalVerdict) -> RunResult:
        await self._configure_workspace(self._stored_workspace(run_id))
        return await _active(self._foreground).respond_approval(run_id, verdict)

    async def respond_approvals(
        self, run_id: str, verdicts: dict[str, ApprovalVerdict]
    ) -> RunResult:
        await self._configure_workspace(self._stored_workspace(run_id))
        return await _active(self._foreground).respond_approvals(run_id, verdicts)

    async def compact(self, run_id: str) -> str:
        """Force-compact the transcript of *run_id* into a summary.

        Returns the new summary text.  Only meaningful when
        ``summarization_enabled`` is true in ``.milky-frog/config.toml``.
        """
        from milky_frog.domain import RunStatus
        from milky_frog.harness.compaction import CompactionHandler

        model = _active(self._model)
        counter = _active(self._counter)
        ck = _active(self._checkpoints)
        try:
            state = ck.load_state(run_id)
        except LookupError:
            raise ValueError(f"Run not found: {run_id}") from None

        result = await CompactionHandler.force_compact(model, counter, state)
        if result is None:
            return state.compaction.summary if state.compaction else ""

        from dataclasses import replace

        state = replace(state, compaction=result.compaction)
        stored = ck.get_run(run_id)
        ck.save_state(run_id, state, status=stored.status if stored else RunStatus.RUNNING)
        # Route the manual /compact through the same hub signal the automatic path
        # uses, so usage accounting and the UI compaction line stay identical.
        await self.hub.run_compaction(run_id, result.messages_folded, result.usage)
        return result.compaction.summary

    def compaction_summary_text(self, run_id: str) -> str:
        """Return the current compaction summary for *run_id* (empty string if none)."""
        ck = _active(self._checkpoints)
        try:
            state = ck.load_state(run_id)
        except LookupError:
            return ""
        return state.compaction.summary if state.compaction else ""

    @staticmethod
    def cancelled_result(run_id: str | None) -> RunResult:
        return ForegroundRun.cancelled_result(run_id)


def _format_skill_injection(
    catalog: SkillCatalog, names: tuple[str, ...]
) -> tuple[str | None, tuple[str, ...]]:
    """Load named skills and format them for eager system-prompt injection.

    Returns the injection text (``None`` when nothing resolved) and the names
    that loaded successfully, so callers record exactly what was injected.
    """
    parts: list[str] = []
    loaded: list[str] = []
    for name in names:
        try:
            skill = catalog.load(name)
        except KeyError:
            logger.warning("selected skill %r not found; skipping", name)
            continue
        loaded.append(name)
        parts.append(f'<active_skill name="{name}">\n{skill.instructions.strip()}\n</active_skill>')
    if not parts:
        return None, ()
    intro = (
        "The following skill has been activated for this Run."
        if len(parts) == 1
        else "The following skills have been activated for this Run."
    )
    body = intro + " Follow these instructions throughout the task.\n\n" + "\n\n".join(parts)
    return body, tuple(loaded)
