from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from milky_frog.checkpoint import CheckpointStore, StoredRun
from milky_frog.domain import ResumeError, RunState, RunStatus
from milky_frog.harness.sandbox import Sandbox
from milky_frog.harness.state import append_user_message, seal


@dataclass(frozen=True, slots=True)
class PreparedRun:
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
        return stored

    def prepare(
        self,
        run_id: str,
        stored: StoredRun,
        *,
        sandbox: Sandbox,
        prompt: str | None,
        updated_at: datetime,
    ) -> PreparedRun:
        state = self._checkpoints.load_state(run_id)
        if stored.status is not RunStatus.WAITING_FOR_APPROVAL:
            state, _repaired = seal(state)

        if prompt is not None:
            state = append_user_message(state, prompt)

        self._checkpoints.prepare_resume(run_id, updated_at, state)
        return PreparedRun(state=state, sandbox=sandbox)
