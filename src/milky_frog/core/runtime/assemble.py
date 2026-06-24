from __future__ import annotations

from milky_frog.adapters.local import LocalSandbox
from milky_frog.checkpoint import CheckpointStore
from milky_frog.core.sandbox import SandboxFactory
from milky_frog.core.session_tool_policy import SessionToolPolicy
from milky_frog.events import EventHub
from milky_frog.events.loop import AgentLoop
from milky_frog.events.tool_step import ToolStepExecutor
from milky_frog.harness.harness import AgentHarness
from milky_frog.harness.tokens import TokenBudget
from milky_frog.harness.tools import ToolRegistry
from milky_frog.harness.tools.builtins import default_tools
from milky_frog.models import Model, RetryingModel


def assemble_agent_harness(
    model: Model,
    checkpoints: CheckpointStore,
    hub: EventHub,
    *,
    tools: ToolRegistry | None = None,
    sandbox_factory: SandboxFactory = LocalSandbox,
) -> AgentHarness:
    """Wire the Harness runtime stack — shared by ``AgentSession`` and tests."""
    registry = tools if tools is not None else ToolRegistry(default_tools())
    policy = SessionToolPolicy(registry)
    tool_step = ToolStepExecutor(registry, hub.emitter, policy)

    async def on_model_retry(run_id: str, message: str) -> None:
        await hub.run_notice(run_id, message, level="warning")

    agent_loop = AgentLoop(RetryingModel(model, on_model_retry), registry, hub, tool_step)
    return AgentHarness(
        checkpoints=checkpoints,
        hub=hub,
        agent_loop=agent_loop,
        tool_step=tool_step,
        policy=policy,
        sandbox_factory=sandbox_factory,
        budget=TokenBudget(),
    )
