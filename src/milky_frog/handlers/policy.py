"""Handler that enforces session-level tool policy via ``HandlerContext``.

Reads ``SessionToolPolicy`` from ``ctx.policy`` on every ``RunBeforeTool``
event so policy changes (``auto_approve()``, per-tool overrides) take effect
without any additional wiring.
"""

from __future__ import annotations

from milky_frog.domain import ToolDecision
from milky_frog.handlers.context import ApprovalResult, BlockResult, HandlerContext, HandlerResult
from milky_frog.handlers.events import RunBeforeTool
from milky_frog.handlers.hub import BaseHandler, EventHub


class PolicyHandler(BaseHandler):
    """Enforces session-level tool policy on ``RunBeforeTool`` events.

    The policy is always read from ``ctx.policy`` — a mutable
    ``SessionToolPolicy`` set by ``AgentSession`` on the hub context.
    """

    def register(self, hub: EventHub) -> None:
        hub.on(RunBeforeTool)(self._on_before_tool)

    async def _on_before_tool(
        self, event: RunBeforeTool, ctx: HandlerContext
    ) -> HandlerResult | None:
        if ctx.policy is None:
            return None
        decision = ctx.policy.decide(event.call)
        if decision is ToolDecision.DENY:
            return BlockResult(reason="denied by tool policy")
        if decision is ToolDecision.NEEDS_APPROVAL:
            return ApprovalResult()
        return None
