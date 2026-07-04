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
    skill_content: str | None = None


@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: str
    status: RunStatus
    final_message: str
    model_calls: int
    usage: RunUsage = field(default_factory=RunUsage)


@dataclass(frozen=True, slots=True)
class CompactionState:
    """A summary of the oldest part of the transcript, as a derived cache.

    ``messages[:through_index]`` are summarized by ``summary``; those original
    messages are **not** deleted from ``RunState.messages`` (the snapshot stays
    the full truth). The summary only replaces them when assembling the request
    sent to the model. Because the summarized prefix is immutable (the transcript
    is append-only), ``through_index`` stays valid across resume.
    """

    summary: str
    through_index: int


@dataclass(frozen=True, slots=True)
class Compacted:
    """A ``RunBeforeModel`` Handler's proposal to compact the transcript prefix.

    A Handler returns this from its callback; the loop applies it by folding
    ``compaction`` into ``RunState`` before assembling the next model request.
    The original messages are never deleted — the snapshot stays the full truth.
    """

    compaction: CompactionState


type HandlerResult = Compacted
"""A control proposal a Handler returns from a lifecycle callback for the loop to
apply. Today the only variant is ``Compacted`` (from ``RunBeforeModel``); add a
union member when a second control point (e.g. tool authorization) lands."""


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
    compaction: CompactionState | None = None
    run_extra: tuple[str, ...] = ()  # ephemeral extra system-prompt sections, not persisted
