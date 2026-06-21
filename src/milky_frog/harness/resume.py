from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from milky_frog.checkpoint import CheckpointStore, StoredRun
from milky_frog.domain import MessageRole, RunState, SteeringChannel
from milky_frog.harness.sandbox import Sandbox
from milky_frog.harness.state import append_user_message, seal
from milky_frog.harness.steering import SteeringPolicy


class ResumeError(Exception):
    """A Run cannot be advanced as requested: unknown, has no pending work and
    no prompt was given, or is still active and cannot accept new input."""


def completed_tail(state: RunState) -> str | None:
    if not state.messages:
        return None
    tail = state.messages[-1]
    if tail.role is MessageRole.ASSISTANT and not tail.tool_calls:
        return tail.content
    return None


@dataclass(frozen=True, slots=True)
class CompletedShortcut:
    """In-memory resume state when the transcript already ends with a clean reply."""

    state: RunState
    needs_persist: bool
    tail: str


def _prepare_completed_shortcut(
    state: RunState,
    steering: SteeringChannel | None,
    policy: SteeringPolicy,
) -> CompletedShortcut | None:
    tail = completed_tail(state)
    if tail is None:
        return None
    steered = policy.drain_for_resume(state, steering)
    return CompletedShortcut(
        state=steered,
        needs_persist=steered is not state,
        tail=tail,
    )


@dataclass(frozen=True, slots=True)
class CompleteShortcutPlan:
    """Finalize a clean assistant tail, or advance if late steering arrives."""

    state: RunState
    sandbox: Sandbox
    tail: str
    kind: Literal["complete_shortcut"] = "complete_shortcut"


@dataclass(frozen=True, slots=True)
class AdvancePlan:
    """``prepare_resume`` state is committed; advance the loaded state."""

    state: RunState
    sandbox: Sandbox
    kind: Literal["advance"] = "advance"


ResumePlan = CompleteShortcutPlan | AdvancePlan


class ResumeGate:
    """Prepare an existing Run for the next ``Harness._advance`` call."""

    def __init__(
        self,
        checkpoints: CheckpointStore,
        steering: SteeringPolicy,
    ) -> None:
        self._checkpoints = checkpoints
        self._steering = steering

    @staticmethod
    def validate(stored: StoredRun | None, run_id: str, prompt: str | None) -> StoredRun:
        if stored is None:
            raise ResumeError(f"unknown Run: {run_id}")
        if prompt is None and not stored.status.is_resumable:
            raise ResumeError(
                f"Run {run_id} is {stored.status.value} with no pending work; "
                "provide a prompt to continue it"
            )
        if prompt is not None and not stored.status.is_continuable:
            raise ResumeError(f"Run {run_id} is {stored.status.value} and cannot accept new input")
        return stored

    def prepare(
        self,
        run_id: str,
        stored: StoredRun,
        *,
        sandbox: Sandbox,
        prompt: str | None,
        steering: SteeringChannel | None,
        updated_at: datetime,
    ) -> ResumePlan:
        state = self._checkpoints.load_state(run_id)
        state, _repaired = seal(state)

        if prompt is not None:
            state = append_user_message(state, prompt)

        if prompt is None:
            shortcut = _prepare_completed_shortcut(state, steering, self._steering)
            if shortcut is not None:
                if not shortcut.needs_persist:
                    return CompleteShortcutPlan(
                        state=shortcut.state,
                        sandbox=sandbox,
                        tail=shortcut.tail,
                    )
                self._checkpoints.prepare_resume(run_id, updated_at, shortcut.state)
                return AdvancePlan(state=shortcut.state, sandbox=sandbox)

        self._checkpoints.prepare_resume(run_id, updated_at, state)
        return AdvancePlan(state=state, sandbox=sandbox)
