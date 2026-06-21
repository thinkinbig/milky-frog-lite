from __future__ import annotations

from typing import Protocol

from milky_frog.domain import ToolCall, ToolDecision


class ToolPolicy(Protocol):
    """Decide whether a tool call is allowed, denied, or needs approval."""

    def decide(self, call: ToolCall) -> ToolDecision: ...


class DefaultToolPolicy:
    """Built-in policy: read/list_dir always allowed; everything else needs approval."""

    _ALWAYS_ALLOW: frozenset[str] = frozenset({"read", "list_dir"})

    def decide(self, call: ToolCall) -> ToolDecision:
        if call.name in self._ALWAYS_ALLOW:
            return ToolDecision.ALLOW
        return ToolDecision.NEEDS_APPROVAL


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


class ToolGate:
    """Permission gate for tool execution.

    Maintains an in-memory approval cache shared between the Harness and CLI
    via the same ``MilkyFrog`` instance. On resume, previously approved or
    denied calls skip the policy check.
    """

    def __init__(self, policy: ToolPolicy | None = None) -> None:
        self._policy: ToolPolicy = policy or DefaultToolPolicy()
        self._decisions: dict[str, bool] = {}

    def check(self, call: ToolCall) -> ToolDecision:
        if call.id in self._decisions:
            return ToolDecision.ALLOW if self._decisions[call.id] else ToolDecision.DENY
        return self._policy.decide(call)

    def approve(self, call_id: str) -> None:
        self._decisions[call_id] = True

    def deny(self, call_id: str) -> None:
        self._decisions[call_id] = False

    def clear(self) -> None:
        self._decisions.clear()
