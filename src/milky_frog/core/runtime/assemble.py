from __future__ import annotations

from collections.abc import Sequence

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
from milky_frog.settings import Settings
from milky_frog.tokens import TokenCounter


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
) -> AgentHarness:
    """Wire the Harness runtime stack — shared by ``AgentSession`` and tests."""
    registry = tools if tools is not None else ToolRegistry(default_tools())
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
