"""Session-level mutable tool policy bound to a ``ToolRegistry``.

``SessionToolPolicy`` reads each tool's approval attributes from the same
registry the Harness executes against, so policy and execution never diverge.
``ToolStepExecutor`` calls ``decide()`` inline before each tool execution.
"""

from __future__ import annotations

from milky_frog.domain import ToolCall, ToolDecision
from milky_frog.harness.tools.base import Tool
from milky_frog.harness.tools.registry import ToolRegistry, UnknownToolError


class SessionToolPolicy:
    """Mutable session-level tool policy for one ``ToolRegistry``.

    Wired at assembly time by ``make_agent_harness``; exposed as
    ``session.policy``.  ``ToolStepExecutor`` calls ``decide()`` inline before
    each tool execution, so changes take effect immediately.

    Default behaviour reads each tool's ``requires_approval`` attribute.
    Per-tool overrides (``allow`` / ``deny`` / ``require_approval``) and
    ``auto_approve()`` take precedence.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._mode: str = "default"  # "default" | "auto_approve"
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
        if call.name in self._overrides:
            return self._overrides[call.name]
        if self._mode == "auto_approve":
            return ToolDecision.ALLOW
        try:
            tool = self._registry.get(call.name)
        except UnknownToolError:
            return ToolDecision.NEEDS_APPROVAL
        if call_needs_approval(tool, call):
            return ToolDecision.NEEDS_APPROVAL
        return ToolDecision.ALLOW


def approval_free_tool_names(registry: ToolRegistry) -> frozenset[str]:
    """Return tool names that never need approval (``requires_approval`` is false)."""
    return frozenset(
        tool.name for tool in registry.tools() if not getattr(tool, "requires_approval", True)
    )


def call_needs_approval(tool: Tool, call: ToolCall) -> bool:
    """Return whether a concrete tool call should pause for user approval."""
    if getattr(tool, "requires_approval", True) is False:
        return False
    per_call = getattr(tool, "needs_approval_for_call", None)
    if per_call is not None:
        return bool(per_call(call.arguments))
    return True
