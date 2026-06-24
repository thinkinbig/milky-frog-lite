from __future__ import annotations

from typing import Protocol

from milky_frog.domain import ToolCall, ToolDecision


class ToolPolicy(Protocol):
    """Seam for per-Tool authorization decisions.

    ``ToolStepExecutor`` calls ``decide()`` inline before each tool execution.
    Concrete adapters (e.g. ``SessionToolPolicy`` in ``core/session_tool_policy.py``)
    bind to a ``ToolRegistry``.
    """

    def decide(self, call: ToolCall) -> ToolDecision: ...

    def auto_approve(self) -> None: ...

    def reset(self) -> None: ...

    def require_approval(self, tool_name: str) -> None: ...

    def deny(self, tool_name: str) -> None: ...

    def allow(self, tool_name: str) -> None: ...
