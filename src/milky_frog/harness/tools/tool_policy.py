"""AgentSession-level mutable tool policy and decision helpers.

Previously had a ``ToolPolicy`` Protocol, ``DefaultToolPolicy``, etc. All
consolidated into ``SessionToolPolicy`` — the one policy class that is owned
by ``AgentSession``, exposed as ``session.policy``, and read by ``PolicyHandler``
from ``HandlerContext`` on every ``RunBeforeTool`` event.
"""

from __future__ import annotations

from milky_frog.domain import ToolCall, ToolDecision
from milky_frog.harness.tools.base import Tool


class SessionToolPolicy:
    """Mutable session-level tool policy.

    Owned by ``AgentSession``; exposed as ``session.policy``.  ``PolicyHandler``
    reads its current state from ``HandlerContext.policy`` on every
    ``RunBeforeTool`` event, so changes take effect immediately.

    Default behaviour reads each tool's ``requires_approval`` attribute.
    Per-tool overrides (``allow`` / ``deny`` / ``require_approval``) and
    ``auto_approve()`` take precedence.
    """

    def __init__(self, tools: tuple[Tool, ...] | None = None) -> None:
        if tools is None:
            from milky_frog.harness.tools.builtins import (
                default_tools,
            )

            tools = default_tools()
        self._tools_by_name: dict[str, Tool] = {tool.name: tool for tool in tools}
        self._mode: str = "default"  # "default" | "permissive"
        self._overrides: dict[str, ToolDecision] = {}

    def auto_approve(self) -> None:
        """Auto-approve any tool that would normally require approval.

        Tools with explicit ``deny()`` overrides are still denied.
        ``reset()`` restores per-tool prompting.
        """
        self._mode = "auto_approve"

    def reset(self) -> None:
        self._mode = "default"
        self._overrides.clear()

    def require_approval(self, tool_name: str) -> None:
        self._overrides[tool_name] = ToolDecision.NEEDS_APPROVAL

    def deny(self, tool_name: str) -> None:
        self._overrides[tool_name] = ToolDecision.DENY

    def allow(self, tool_name: str) -> None:
        self._overrides[tool_name] = ToolDecision.ALLOW

    def decide(self, call: ToolCall) -> ToolDecision:
        # Explicit overrides always win.
        if call.name in self._overrides:
            return self._overrides[call.name]
        # Auto-approve: skip NEEDS_APPROVAL, fall through to baseline.
        if self._mode == "auto_approve":
            tool = self._tools_by_name.get(call.name)
            if tool is not None and not call_needs_approval(tool, call):
                return ToolDecision.ALLOW
            return ToolDecision.ALLOW
        # Default: prompt for any tool that needs approval.
        tool = self._tools_by_name.get(call.name)
        if tool is None or call_needs_approval(tool, call):
            return ToolDecision.NEEDS_APPROVAL
        return ToolDecision.ALLOW


# ── decision helpers ──────────────────────────────────────────────────────


def approval_free_tool_names(tools: tuple[Tool, ...]) -> frozenset[str]:
    """Return tool names that never need approval (``requires_approval`` is false)."""
    return frozenset(tool.name for tool in tools if not getattr(tool, "requires_approval", True))


def call_needs_approval(tool: Tool, call: ToolCall) -> bool:
    """Return whether a concrete tool call should pause for user approval."""
    if getattr(tool, "requires_approval", True) is False:
        return False
    per_call = getattr(tool, "needs_approval_for_call", None)
    if per_call is not None:
        return bool(per_call(call.arguments))
    return True
