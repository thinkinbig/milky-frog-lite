from __future__ import annotations

from collections.abc import Callable

from milky_frog.domain import RunState, SteeringChannel
from milky_frog.harness.state import append_user_message


class DetachedSteeringChannel:
    """Inert marker used in observational RunStarted snapshots."""

    def drain(self) -> list[str]:
        return []


class SteeringPolicy:
    """Harness-side fold policy for mid-Run steering (ADR-0011).

    Infra adapters read lines into a :class:`~milky_frog.domain.SteeringChannel`;
    this module owns *when* to drain and whether folds are durable Checkpoint
    writes or in-memory resume preparation.
    """

    def __init__(self, append_user_message: Callable[[RunState, str], RunState]) -> None:
        self._append_user_message = append_user_message

    def absorb_turn_boundary(self, state: RunState, steering: SteeringChannel | None) -> RunState:
        """Drain at a turn boundary and persist each line as a user turn.

        Used between model calls, instead of completing, and before persisting
        COMPLETED. Returns the same ``state`` object when nothing was drained.
        """
        if steering is None:
            return state
        for line in steering.drain():
            state = self._append_user_message(state, line)
        return state

    def drain_for_resume(self, state: RunState, steering: SteeringChannel | None) -> RunState:
        """Drain for the resume completed-tail shortcut without persisting yet."""
        if steering is None:
            return state
        for line in steering.drain():
            state = append_user_message(state, line)
        return state

    @staticmethod
    def added_turns(before: RunState, after: RunState) -> bool:
        """True when ``after`` includes steering lines ``before`` did not."""
        return after is not before
