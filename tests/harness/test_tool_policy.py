from __future__ import annotations

from milky_frog.core.session_tool_policy import (
    SessionToolPolicy,
    approval_free_tool_names,
    call_needs_approval,
)
from milky_frog.domain import ToolCall, ToolDecision
from milky_frog.harness.tools import default_tools
from milky_frog.harness.tools.registry import ToolRegistry


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


def _policy(*tools: object) -> SessionToolPolicy:
    return SessionToolPolicy(ToolRegistry(tuple(tools)))  # type: ignore[arg-type]


# ── Init ────────────────────────────────────────────────────────────────


def test_init_binds_to_registry_tools() -> None:
    registry = ToolRegistry(default_tools())
    policy = SessionToolPolicy(registry)

    registry.get("read_file")
    registry.get("write_file")
    assert policy._registry is registry
    assert policy._mode == "default"
    assert policy._overrides == {}


def test_init_with_custom_tools() -> None:
    registry = ToolRegistry((AlwaysNeedsApprovalTool(), NeverNeedsApprovalTool()))
    policy = SessionToolPolicy(registry)

    registry.get("needs_approval")
    registry.get("safe_tool")
    assert policy._mode == "default"


def test_policy_sees_tools_registered_after_construction() -> None:
    registry = ToolRegistry()
    policy = SessionToolPolicy(registry)
    registry.register(NeverNeedsApprovalTool())

    decision = policy.decide(ToolCall("id", "safe_tool", {}))
    assert decision is ToolDecision.ALLOW


# ── auto_approve / reset ────────────────────────────────────────────────


def test_auto_approve_approves_known_tool() -> None:
    policy = _policy(NeverNeedsApprovalTool())
    policy.auto_approve()
    decision = policy.decide(ToolCall("id", "safe_tool", {}))
    assert decision is ToolDecision.ALLOW


def test_auto_approve_approves_unknown_tool() -> None:
    policy = _policy(NeverNeedsApprovalTool())
    policy.auto_approve()
    decision = policy.decide(ToolCall("id", "unknown_tool", {}))
    assert decision is ToolDecision.ALLOW


def test_reset_restores_default_mode() -> None:
    policy = _policy(AlwaysNeedsApprovalTool())
    policy.auto_approve()
    policy.reset()
    decision = policy.decide(ToolCall("id", "needs_approval", {}))
    assert decision is ToolDecision.NEEDS_APPROVAL


def test_reset_clears_overrides() -> None:
    policy = _policy(AlwaysNeedsApprovalTool())
    policy.deny("needs_approval")
    policy.reset()
    decision = policy.decide(ToolCall("id", "needs_approval", {}))
    assert decision is ToolDecision.NEEDS_APPROVAL  # back to default


# ── Per-tool overrides ──────────────────────────────────────────────────


def test_require_approval_override() -> None:
    tool = NeverNeedsApprovalTool()
    policy = _policy(tool)
    policy.require_approval("safe_tool")
    decision = policy.decide(ToolCall("id", "safe_tool", {}))
    assert decision is ToolDecision.NEEDS_APPROVAL


def test_deny_override() -> None:
    tool = NeverNeedsApprovalTool()
    policy = _policy(tool)
    policy.deny("safe_tool")
    decision = policy.decide(ToolCall("id", "safe_tool", {}))
    assert decision is ToolDecision.DENY


def test_deny_override_always_wins_even_in_auto_approve() -> None:
    tool = NeverNeedsApprovalTool()
    policy = _policy(tool)
    policy.deny("safe_tool")
    policy.auto_approve()
    decision = policy.decide(ToolCall("id", "safe_tool", {}))
    assert decision is ToolDecision.DENY


def test_allow_override() -> None:
    tool = AlwaysNeedsApprovalTool()
    policy = _policy(tool)
    policy.allow("needs_approval")
    decision = policy.decide(ToolCall("id", "needs_approval", {}))
    assert decision is ToolDecision.ALLOW


# ── Default decide logic ────────────────────────────────────────────────


def test_default_unknown_tool_needs_approval() -> None:
    policy = _policy()
    decision = policy.decide(ToolCall("id", "unknown", {}))
    assert decision is ToolDecision.NEEDS_APPROVAL


def test_default_needs_approval_tool_prompts() -> None:
    policy = _policy(AlwaysNeedsApprovalTool())
    decision = policy.decide(ToolCall("id", "needs_approval", {}))
    assert decision is ToolDecision.NEEDS_APPROVAL


def test_default_safe_tool_is_allowed() -> None:
    policy = _policy(NeverNeedsApprovalTool())
    decision = policy.decide(ToolCall("id", "safe_tool", {}))
    assert decision is ToolDecision.ALLOW


def test_default_per_call_approval_denies_dangerous() -> None:
    policy = _policy(PerCallApprovalTool())
    decision = policy.decide(ToolCall("id", "per_call", {"dangerous": True}))
    assert decision is ToolDecision.NEEDS_APPROVAL


def test_default_per_call_approval_allows_safe() -> None:
    policy = _policy(PerCallApprovalTool())
    decision = policy.decide(ToolCall("id", "per_call", {"dangerous": False}))
    assert decision is ToolDecision.ALLOW


# ── Helper: approval_free_tool_names ────────────────────────────────────


def test_approval_free_tool_names_returns_safe_tools() -> None:
    registry = ToolRegistry(
        (AlwaysNeedsApprovalTool(), NeverNeedsApprovalTool(), PerCallApprovalTool())
    )
    names = approval_free_tool_names(registry)
    assert names == frozenset({"safe_tool"})


def test_approval_free_tool_names_empty_when_all_require_approval() -> None:
    registry = ToolRegistry((AlwaysNeedsApprovalTool(),))
    names = approval_free_tool_names(registry)
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
