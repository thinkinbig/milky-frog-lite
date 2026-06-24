from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ApprovalDecision(StrEnum):
    """User's verdict on a Run paused for tool approval.

    Threaded into ``Harness.respond_approval`` to release pending tool calls:
    ``APPROVE`` executes them, ``DENY`` seals them with a refusal result.
    """

    APPROVE = "approve"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class ApprovalVerdict:
    """User's verdict on a Run paused for tool approval, with an optional reason."""

    decision: ApprovalDecision
    denial_reason: str | None = None
