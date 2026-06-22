from __future__ import annotations

from milky_frog.domain import ToolCall, ToolDecision
from milky_frog.harness.tools.tool_policy import DefaultToolPolicy, ToolPolicy


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
