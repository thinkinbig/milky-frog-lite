from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from milky_frog.checkpoint import CheckpointStore, StoredRun
from milky_frog.domain import MessageRole, RunState
from milky_frog.harness.sandbox import Sandbox
from milky_frog.harness.state import append_user_message, seal


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
class AdvancePlan:
    """``prepare`` has committed resume state; advance the loaded state."""

    state: RunState
    sandbox: Sandbox


class ResumeGate:
    """Validate and prepare an existing Run for ``Harness._advance``."""

    def __init__(self, checkpoints: CheckpointStore) -> None:
        self._checkpoints = checkpoints

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
        updated_at: datetime,
    ) -> AdvancePlan:
        state = self._checkpoints.load_state(run_id)
        state, _repaired = seal(state)

        if prompt is not None:
            state = append_user_message(state, prompt)

        self._checkpoints.prepare_resume(run_id, updated_at, state)
        return AdvancePlan(state=state, sandbox=sandbox)
