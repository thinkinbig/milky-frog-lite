"""Adapter that wraps a legacy ``ToolPolicy`` as a handler on ``RunBeforeTool``.

Once all callers migrate to registering handlers directly on ``RunBeforeTool``,
this module (and ``ToolPolicy``) can be removed.
"""

from __future__ import annotations

from milky_frog.domain import ToolDecision
from milky_frog.handlers.bus import BaseHandler, LifecycleBus
from milky_frog.handlers.context import ApprovalResult, BlockResult, HandlerContext, HandlerResult
from milky_frog.handlers.events import RunBeforeTool
from milky_frog.harness.tools.tool_policy import DefaultToolPolicy, ToolPolicy


class PolicyHandler(BaseHandler):
    """Wraps a ``ToolPolicy`` as a handler on ``RunBeforeTool`` events.

    Registers itself on the bus so the policy decision is made through
    the same channel as every other handler — no separate gate needed.
    """

    def __init__(self, policy: ToolPolicy | None = None) -> None:
        self._policy: ToolPolicy = policy or DefaultToolPolicy()

    def register(self, registry: LifecycleBus) -> None:
        registry.on(RunBeforeTool)(self._on_before_tool)

    async def _on_before_tool(
        self, event: RunBeforeTool, ctx: HandlerContext
    ) -> HandlerResult | None:
        del ctx
        decision = self._policy.decide(event.call)
        if decision is ToolDecision.DENY:
            return BlockResult(reason="denied by tool policy")
        if decision is ToolDecision.NEEDS_APPROVAL:
            return ApprovalResult()
        return None
