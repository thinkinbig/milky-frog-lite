from __future__ import annotations

from milky_frog.domain import ToolCall, ToolDecision
from milky_frog.harness.tools.tool_policy import (
    SessionToolPolicy,
    approval_free_tool_names,
    call_needs_approval,
)


class AlwaysNeedsApprovalTool:
    name = "needs_approval"
    description = "Always needs approval"
    requires_approval = True

    async def execute(self, context, input): ...


class NeverNeedsApprovalTool:
    name = "safe_tool"
    description = "Never needs approval"
    requires_approval = False

    async def execute(self, context, input): ...


class PerCallApprovalTool:
    name = "per_call"
    description = "Decides per call"
    requires_approval = True

    def needs_approval_for_call(self, arguments: dict) -> bool:
        return arguments.get("dangerous", False)

    async def execute(self, context, input): ...


# ── Init ────────────────────────────────────────────────────────────────


def test_init_with_default_tools() -> None:
    policy = SessionToolPolicy()
    # Should have loaded the default built-in tools
    assert "read_file" in policy._tools_by_name
    assert "write_file" in policy._tools_by_name
    assert policy._mode == "default"
    assert policy._overrides == {}


def test_init_with_custom_tools() -> None:
    policy = SessionToolPolicy(tools=(AlwaysNeedsApprovalTool(), NeverNeedsApprovalTool()))
    assert "needs_approval" in policy._tools_by_name
    assert "safe_tool" in policy._tools_by_name
    assert policy._mode == "default"


# ── auto_approve / reset ────────────────────────────────────────────────


def test_auto_approve_approves_known_tool() -> None:
    policy = SessionToolPolicy(tools=(NeverNeedsApprovalTool(),))
    policy.auto_approve()
    decision = policy.decide(ToolCall("id", "safe_tool", {}))
    assert decision is ToolDecision.ALLOW


def test_auto_approve_approves_unknown_tool() -> None:
    policy = SessionToolPolicy(tools=(NeverNeedsApprovalTool(),))
    policy.auto_approve()
    decision = policy.decide(ToolCall("id", "unknown_tool", {}))
    assert decision is ToolDecision.ALLOW


def test_reset_restores_default_mode() -> None:
    policy = SessionToolPolicy(tools=(AlwaysNeedsApprovalTool(),))
    policy.auto_approve()
    policy.reset()
    decision = policy.decide(ToolCall("id", "needs_approval", {}))
    assert decision is ToolDecision.NEEDS_APPROVAL


def test_reset_clears_overrides() -> None:
    policy = SessionToolPolicy(tools=(AlwaysNeedsApprovalTool(),))
    policy.deny("needs_approval")
    policy.reset()
    decision = policy.decide(ToolCall("id", "needs_approval", {}))
    assert decision is ToolDecision.NEEDS_APPROVAL  # back to default


# ── Per-tool overrides ──────────────────────────────────────────────────


def test_require_approval_override() -> None:
    tool = NeverNeedsApprovalTool()
    policy = SessionToolPolicy(tools=(tool,))
    policy.require_approval("safe_tool")
    decision = policy.decide(ToolCall("id", "safe_tool", {}))
    assert decision is ToolDecision.NEEDS_APPROVAL


def test_deny_override() -> None:
    tool = NeverNeedsApprovalTool()
    policy = SessionToolPolicy(tools=(tool,))
    policy.deny("safe_tool")
    decision = policy.decide(ToolCall("id", "safe_tool", {}))
    assert decision is ToolDecision.DENY


def test_deny_override_always_wins_even_in_auto_approve() -> None:
    tool = NeverNeedsApprovalTool()
    policy = SessionToolPolicy(tools=(tool,))
    policy.deny("safe_tool")
    policy.auto_approve()
    decision = policy.decide(ToolCall("id", "safe_tool", {}))
    assert decision is ToolDecision.DENY


def test_allow_override() -> None:
    tool = AlwaysNeedsApprovalTool()
    policy = SessionToolPolicy(tools=(tool,))
    policy.allow("needs_approval")
    decision = policy.decide(ToolCall("id", "needs_approval", {}))
    assert decision is ToolDecision.ALLOW


# ── Default decide logic ────────────────────────────────────────────────


def test_default_unknown_tool_needs_approval() -> None:
    policy = SessionToolPolicy(tools=())
    decision = policy.decide(ToolCall("id", "unknown", {}))
    assert decision is ToolDecision.NEEDS_APPROVAL


def test_default_needs_approval_tool_prompts() -> None:
    policy = SessionToolPolicy(tools=(AlwaysNeedsApprovalTool(),))
    decision = policy.decide(ToolCall("id", "needs_approval", {}))
    assert decision is ToolDecision.NEEDS_APPROVAL


def test_default_safe_tool_is_allowed() -> None:
    policy = SessionToolPolicy(tools=(NeverNeedsApprovalTool(),))
    decision = policy.decide(ToolCall("id", "safe_tool", {}))
    assert decision is ToolDecision.ALLOW


def test_default_per_call_approval_denies_dangerous() -> None:
    policy = SessionToolPolicy(tools=(PerCallApprovalTool(),))
    decision = policy.decide(ToolCall("id", "per_call", {"dangerous": True}))
    assert decision is ToolDecision.NEEDS_APPROVAL


def test_default_per_call_approval_allows_safe() -> None:
    policy = SessionToolPolicy(tools=(PerCallApprovalTool(),))
    decision = policy.decide(ToolCall("id", "per_call", {"dangerous": False}))
    assert decision is ToolDecision.ALLOW


# ── Helper: approval_free_tool_names ────────────────────────────────────


def test_approval_free_tool_names_returns_safe_tools() -> None:
    tools = (AlwaysNeedsApprovalTool(), NeverNeedsApprovalTool(), PerCallApprovalTool())
    names = approval_free_tool_names(tools)
    assert names == frozenset({"safe_tool"})


def test_approval_free_tool_names_empty_when_all_require_approval() -> None:
    tools = (AlwaysNeedsApprovalTool(),)
    names = approval_free_tool_names(tools)
    assert names == frozenset()


# ── Helper: call_needs_approval ─────────────────────────────────────────


def test_call_needs_approval_tool_with_approval_false() -> None:
    tool = NeverNeedsApprovalTool()
    assert call_needs_approval(tool, ToolCall("id", "safe_tool", {})) is False


def test_call_needs_approval_tool_with_approval_true() -> None:
    tool = AlwaysNeedsApprovalTool()
    assert call_needs_approval(tool, ToolCall("id", "needs_approval", {})) is True


def test_call_needs_approval_per_call_dangerous() -> None:
    tool = PerCallApprovalTool()
    assert call_needs_approval(tool, ToolCall("id", "per_call", {"dangerous": True})) is True


def test_call_needs_approval_per_call_safe() -> None:
    tool = PerCallApprovalTool()
    assert call_needs_approval(tool, ToolCall("id", "per_call", {"dangerous": False})) is False


def test_call_needs_approval_no_needs_approval_for_call() -> None:
    tool = AlwaysNeedsApprovalTool()
    assert call_needs_approval(tool, ToolCall("id", "needs_approval", {})) is True


def test_call_needs_approval_default_true_when_no_requires_approval() -> None:
    """A Tool without ``requires_approval`` attr should default to needing approval."""

    class PlainTool:
        name = "plain"
        description = "no requires_approval attr"

        async def execute(self, context, input): ...

    tool = PlainTool()
    assert call_needs_approval(tool, ToolCall("id", "plain", {})) is True
