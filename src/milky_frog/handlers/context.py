from __future__ import annotations

from dataclasses import dataclass

from milky_frog.domain import ModelRequest
from milky_frog.harness.tools.tool_policy import SessionToolPolicy


@dataclass(frozen=True, slots=True)
class BlockResult:
    """Return from a ``BeforeTool`` handler to deny execution."""

    reason: str


@dataclass(frozen=True, slots=True)
class ApprovalResult:
    """Return from a handler to pause the Run for user approval."""

    reason: str = "needs approval"


@dataclass(frozen=True, slots=True)
class SystemPromptSection:
    """Return from a ``RunBeforeStart`` handler to inject content into the system prompt.

    Sections are appended after the base system prompt in registration order.
    """

    content: str


@dataclass(frozen=True, slots=True)
class BudgetedRequest:
    """Return from a ``RunBeforeModel`` handler to trim the request to a token budget.

    The Harness applies this rewrite after collecting handler results, replacing
    the original request with the budgeted one before sending to the model.
    """

    request: ModelRequest


type HandlerResult = BlockResult | ApprovalResult | SystemPromptSection | BudgetedRequest
"""Union of all result types a handler may return to control Harness execution.

A handler that returns ``None`` is pure observation; returning a
``HandlerResult`` signals intent to block, pause, extend, or rewrite the current step.
"""


@dataclass(frozen=True, slots=True)
class HandlerContext:
    """Framework-managed resources passed to every handler at notify time.

    Populated by ``AgentSession`` via ``EventHub.set_context`` so handlers
    can access mutable runtime state without being coupled to AgentSession.
    """

    policy: SessionToolPolicy | None = None
