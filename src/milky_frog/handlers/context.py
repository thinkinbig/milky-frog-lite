from __future__ import annotations

from dataclasses import dataclass


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


type HandlerResult = BlockResult | ApprovalResult | SystemPromptSection
"""Union of all result types a handler may return to control Harness execution.

A handler that returns ``None`` is pure observation; returning a
``HandlerResult`` signals intent to block, pause, or extend the current step.
"""


@dataclass(frozen=True, slots=True)
class HandlerContext:
    """Framework-managed resources passed to every handler at notify time.

    Handlers receive this alongside the event. Empty today; reserved for
    shared read-only services injected via ``LifecycleBus.set_context``.
    """
