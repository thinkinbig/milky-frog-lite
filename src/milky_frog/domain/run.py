from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from milky_frog.domain.messages import Message
from milky_frog.domain.status import RunStatus
from milky_frog.domain.usage import RunUsage

DEFAULT_MAX_MODEL_CALLS = 30


@dataclass(slots=True)
class RunCancellation:
    """Cooperative cancellation token for a foreground Run."""

    _cancelled: bool = field(default=False, repr=False)

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled


class ToolRunCancelled(Exception):
    """Cooperative cancel arrived while a Tool was executing."""


def is_cancelled(cancellation: RunCancellation | None) -> bool:
    return cancellation is not None and cancellation.is_cancelled


@dataclass(frozen=True, slots=True)
class RunRequest:
    prompt: str
    workspace: Path
    max_model_calls: int = DEFAULT_MAX_MODEL_CALLS
    cancellation: RunCancellation | None = None


@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: str
    status: RunStatus
    final_message: str
    model_calls: int
    usage: RunUsage = field(default_factory=RunUsage)


@dataclass(frozen=True, slots=True)
class RunState:
    """The live transcript and accounting of one Run, threaded through the loop.

    Also the durable Checkpoint snapshot: the Harness grows this value in memory and
    persists it after each meaningful step. ``resume`` loads the same shape rather
    than replaying an event log.
    """

    run_id: str
    workspace: Path
    messages: tuple[Message, ...] = ()
    completed_model_calls: int = 0
    reasoning_log: tuple[str, ...] = ()
    usage: RunUsage = field(default_factory=RunUsage)
