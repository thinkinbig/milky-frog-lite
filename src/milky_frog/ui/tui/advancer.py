from __future__ import annotations

from pathlib import Path

from milky_frog.domain import (
    ApprovalDecision,
    RunCancellation,
    RunRequest,
    RunResult,
    RunStatus,
)
from milky_frog.project import load_project_config
from milky_frog.runtime import MilkyFrog


class PreRunError(Exception):
    """The Harness was not started (for example an unknown Run id)."""


class RunAdvancer:
    """Orchestration state for one interactive TUI conversation.

    Holds ``run_id``, busy flag, cancellation token, and pending-approval run_id.
    Drives ``frog.harness`` only — UI updates travel through ``TuiPresentationHandler``
    on the lifecycle bus (or ``RunError`` for pre-harness failures in the App worker).
    """

    def __init__(self, frog: MilkyFrog) -> None:
        self.frog = frog
        self.run_id: str | None = None
        self.busy: bool = False
        self.cancellation: RunCancellation | None = None
        self.pending_approval: str | None = None

    def begin(self) -> RunCancellation:
        """Mark as busy and mint a fresh cancellation token for the caller."""
        self.busy = True
        token = RunCancellation()
        self.cancellation = token
        return token

    def cancel(self) -> None:
        """Cooperatively signal the in-flight Run to stop."""
        if self.cancellation is not None:
            self.cancellation.cancel()

    def resolve_approval(self, answer: str) -> ApprovalDecision | None:
        """Parse a y/n reply; return ``None`` when the answer is unrecognised."""
        if answer in {"y", "yes", "a", "approve"}:
            return ApprovalDecision.APPROVE
        if answer in {"n", "no", "d", "deny"}:
            return ApprovalDecision.DENY
        return None

    async def do_run(self, task: str, run_id: str | None) -> RunResult:
        """Advance the harness (new Run or resume with a prompt)."""
        cancellation = self.cancellation
        frog = self.frog
        try:
            if run_id is None:
                config = load_project_config(Path.cwd())
                result = await frog.harness.run(
                    RunRequest(
                        task,
                        Path.cwd(),
                        max_model_calls=config.max_model_calls,
                        cancellation=cancellation,
                    )
                )
            else:
                stored = frog.checkpoints.get_run(run_id)
                if stored is None:
                    raise PreRunError(f"unknown Run: {run_id}")
                config = load_project_config(stored.workspace)
                result = await frog.harness.resume(
                    run_id,
                    max_model_calls=config.max_model_calls,
                    prompt=task,
                    cancellation=cancellation,
                )
            self.run_id = result.run_id
            return result
        finally:
            self.busy = False
            self.cancellation = None

    async def do_approve(self, run_id: str, decision: ApprovalDecision) -> RunResult:
        """Resume a paused Run with the user's approval verdict."""
        cancellation = self.cancellation
        frog = self.frog
        try:
            stored = frog.checkpoints.get_run(run_id)
            if stored is None:
                raise PreRunError(f"unknown Run: {run_id}")
            config = load_project_config(stored.workspace)
            result = await frog.harness.resume(
                run_id,
                max_model_calls=config.max_model_calls,
                approval=decision,
                cancellation=cancellation,
            )
            self.run_id = result.run_id
            return result
        finally:
            self.busy = False
            self.cancellation = None

    @staticmethod
    def cancelled_result(run_id: str | None) -> RunResult:
        """Synthetic result when a Textual worker is hard-cancelled."""
        return RunResult(run_id or "unknown", RunStatus.CANCELLED, "cancelled", 0)
