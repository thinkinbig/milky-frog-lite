"""Tool-approval policy: the Protocol, adapters, and decision helpers.

Previously split across ``gates/tool.py`` (policy adapters) and
``harness/tools/registry.py`` (``approval_free_tool_names`` /
``call_needs_approval``). Consolidated here so the policy and the tool
attributes it reads live in one module.
"""

from __future__ import annotations

from typing import Protocol

from milky_frog.domain import ToolCall, ToolDecision
from milky_frog.harness.tools.base import Tool


class ToolPolicy(Protocol):
    """Decide whether a tool call is allowed, denied, or needs approval."""

    def decide(self, call: ToolCall) -> ToolDecision: ...


class DefaultToolPolicy:
    """Built-in policy: read-only Tools allowed; mutating calls need approval."""

    def __init__(self, tools: tuple[Tool, ...] | None = None) -> None:
        if tools is None:
            from milky_frog.harness.tools.builtins import default_tools  # avoid circular import

            tools = default_tools()
        self._tools = {tool.name: tool for tool in tools}

    def decide(self, call: ToolCall) -> ToolDecision:
        tool = self._tools.get(call.name)
        if tool is None or call_needs_approval(tool, call):
            return ToolDecision.NEEDS_APPROVAL
        return ToolDecision.ALLOW


class PermissivePolicy:
    """Policy that allows every tool call — useful in tests."""

    def decide(self, call: ToolCall) -> ToolDecision:
        del call
        return ToolDecision.ALLOW


class DenyAllPolicy:
    """Policy that denies every tool call."""

    def decide(self, call: ToolCall) -> ToolDecision:
        del call
        return ToolDecision.DENY


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
