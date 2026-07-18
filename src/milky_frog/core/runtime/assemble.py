from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from milky_frog.adapters.local import LocalSandbox
from milky_frog.checkpoint import CheckpointStore
from milky_frog.core.sandbox import SandboxFactory
from milky_frog.core.session_tool_policy import SessionToolPolicy
from milky_frog.events import EventHub
from milky_frog.events.hub import Handler
from milky_frog.events.loop import AgentLoop
from milky_frog.events.tool_step import ToolStepExecutor
from milky_frog.handlers.checkpoint import CheckpointHandler
from milky_frog.handlers.langfuse import LangfuseHandler
from milky_frog.harness.budget import TokenBudget
from milky_frog.harness.context import ContextManager
from milky_frog.harness.harness import AgentHarness
from milky_frog.harness.prompt_context import ContextLoader
from milky_frog.harness.tools import ToolRegistry
from milky_frog.harness.tools.builtins import default_tools
from milky_frog.models import Model, RetryingModel
from milky_frog.project import ProjectConfig
from milky_frog.settings import Settings
from milky_frog.tokens import TokenCounter


@dataclass(frozen=True, slots=True)
class HarnessAssembly:
    """Shared ingredients for every Harness owned by one AgentSession.

    Run variants choose only their Tool registry, approval mode, and (for a
    write nested Run) a Workspace-specific Sandbox factory. The model,
    Checkpoint, lifecycle hub, context, token, and retry wiring stay identical
    by construction.
    """

    model: Model
    checkpoints: CheckpointStore
    hub: EventHub
    sandbox_factory: SandboxFactory = LocalSandbox
    context_loader: ContextLoader | None = None
    token_counter: TokenCounter | None = None
    max_retries: int = 3
    retry_base_delay: float = 1.0
    home: Path | None = None
    """Agent home directory; gates the ``load_skill`` Tool (see ``default_tools``)."""

    def make_harness(
        self,
        tools: ToolRegistry,
        *,
        sandbox_factory: SandboxFactory | None = None,
        auto_approve: bool = False,
    ) -> AgentHarness:
        """Build one Harness variant without repeating shared composition."""
        selected_sandbox_factory = (
            self.sandbox_factory if sandbox_factory is None else sandbox_factory
        )
        harness = make_agent_harness(
            self.model,
            self.checkpoints,
            self.hub,
            tools=tools,
            sandbox_factory=selected_sandbox_factory,
            context_loader=self.context_loader,
            token_counter=self.token_counter,
            max_retries=self.max_retries,
            retry_base_delay=self.retry_base_delay,
        )
        if auto_approve:
            harness.policy.auto_approve()
        return harness


@dataclass(frozen=True, slots=True)
class HarnessRuntime:
    """Foreground Harness plus the mutable Tool registry shared with MCP."""

    foreground: AgentHarness
    registry: ToolRegistry


def make_harness_runtime(
    assembly: HarnessAssembly,
    *,
    jina_api_key: str | None = None,
) -> HarnessRuntime:
    """Assemble the foreground and nested Run variants for one AgentSession."""
    # Local import keeps the high-level runtime constructor here without making
    # SubagentRuntime and its HarnessAssembly dependency an import cycle.
    from milky_frog.core.runtime.subagent import SubagentRuntime
    from milky_frog.harness.tools.builtins import SubagentTool

    subagent_runner = SubagentRuntime(assembly, jina_api_key=jina_api_key)
    # SubagentTool must be a constructor Tool (not registered later) so MCP
    # reloads preserve it as a builtin.
    registry = ToolRegistry(
        (
            *default_tools(jina_api_key=jina_api_key, home=assembly.home),
            SubagentTool(subagent_runner),
        )
    )
    return HarnessRuntime(
        foreground=assembly.make_harness(registry),
        registry=registry,
    )


def make_sandbox_factory(config: ProjectConfig) -> SandboxFactory:
    """Pick the Sandbox adapter named by ``[sandbox].kind`` in the project config.

    ``local`` (default) returns the ``LocalSandbox`` class itself — it already
    satisfies ``SandboxFactory`` via its ``(workspace)`` constructor.
    """
    if config.sandbox.kind == "docker":
        from milky_frog.adapters.docker import DockerSandboxFactory

        image = config.sandbox.image
        if image is None:  # pragma: no cover - SandboxConfig validation guarantees this
            raise ValueError("sandbox.image is required when sandbox.kind = 'docker'")
        return DockerSandboxFactory(
            image=image,
            workspace_mount=config.sandbox.workspace_mount,
            mask_paths=config.sandbox.mask_paths,
            config=config,
        )
    return LocalSandbox


def make_session_handlers(
    settings: Settings,
    checkpoints: CheckpointStore,
    *,
    extra: Sequence[Handler] = (),
) -> list[Handler]:
    """Assemble every lifecycle handler for a session, in one place.

    Returns handlers in registration order. ``CheckpointHandler`` declares
    its own priority (100) so it always persists before other observers
    regardless of list position. The caller registers each handler on the
    hub and owns their lifetime — every returned handler is entered on
    session open and released when the runtime closes the session.
    """
    handlers: list[Handler] = [
        CheckpointHandler(checkpoints),
    ]
    handlers.extend(extra)
    langfuse = LangfuseHandler.from_settings(settings)
    if langfuse is not None:
        handlers.append(langfuse)
    return handlers


def make_agent_harness(
    model: Model,
    checkpoints: CheckpointStore,
    hub: EventHub,
    *,
    tools: ToolRegistry | None = None,
    sandbox_factory: SandboxFactory = LocalSandbox,
    context_loader: ContextLoader | None = None,
    token_counter: TokenCounter | None = None,
    max_retries: int = 3,
    retry_base_delay: float = 1.0,
    jina_api_key: str | None = None,
) -> AgentHarness:
    """Wire the Harness runtime graph shared by ``AgentSession`` and tests."""
    registry = (
        tools if tools is not None else ToolRegistry(default_tools(jina_api_key=jina_api_key))
    )
    policy = SessionToolPolicy(registry)
    budget = TokenBudget(counter=token_counter)
    tool_step = ToolStepExecutor(registry, hub.emitter, policy, budget=budget)

    async def on_model_retry(run_id: str, message: str) -> None:
        await hub.run_notice(run_id, message, level="warning")

    agent_loop = AgentLoop(
        RetryingModel(model, on_model_retry, max_attempts=max_retries, base_delay=retry_base_delay),
        registry,
        hub,
        tool_step,
        ContextManager(context_loader),
    )
    return AgentHarness(
        checkpoints=checkpoints,
        hub=hub,
        agent_loop=agent_loop,
        tool_step=tool_step,
        policy=policy,
        sandbox_factory=sandbox_factory,
        budget=budget,
    )
