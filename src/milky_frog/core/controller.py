from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from milky_frog.checkpoint import StoredRun
from milky_frog.core.runtime.checkpoint import RunCheckpointFacade
from milky_frog.domain import RunStatus
from milky_frog.harness.state import unmatched_tool_calls


@dataclass(frozen=True, slots=True)
class ResumePlan:
    """Outcome of parsing a resume/attach request."""

    run_id: str
    prompt: str | None = None
    advance_pending: bool = False


@dataclass(frozen=True, slots=True)
class AttachOutcome:
    """What the UI should do after attaching to a Run."""

    run_id: str
    kind: str  # "prompt_continue" | "approval_pending" | "advance" | "attached"
    tool_name: str = ""
    approval_reason: str = ""


class RunController:
    """Foreground Run control: resume parsing, attach, checkpoint reads.

    Keeps resume/attach logic out of the Textual presentation layer.
    """

    def __init__(self, checkpoints: RunCheckpointFacade) -> None:
        self._checkpoints = checkpoints

    def workspace_runs(self, workspace: Path) -> tuple[StoredRun, ...]:
        """Return recent Runs for one Workspace in Checkpoint update order."""
        return self._checkpoints.list_runs(workspace=workspace)

    def parse_resume_command(self, task: str) -> ResumePlan | str:
        """Parse ``/resume`` variants. Returns an error message string on failure."""
        rest = task[len("/resume") :].strip()
        head, _, tail = rest.partition(" ")
        head = head.strip()
        tail = tail.strip()

        if head:
            try:
                run_id = self._checkpoints.resolve_run_id(head)
            except LookupError as error:
                return f"unknown Run: {error}"
            except ValueError as error:
                return f"unknown Run: {error}"
        else:
            runs = self._checkpoints.list_runs(limit=1)
            if not runs:
                return "No runs found to resume."
            run_id = runs[0].run_id

        return ResumePlan(run_id=run_id, prompt=tail or None)

    def attach(
        self,
        run_id: str,
        *,
        prompt: str | None = None,
        advance_pending: bool = False,
    ) -> AttachOutcome:
        if prompt is not None:
            return AttachOutcome(run_id=run_id, kind="prompt_continue")

        stored = self._checkpoints.get_run(run_id)
        if stored is not None and stored.status is RunStatus.WAITING_FOR_APPROVAL:
            state = self._checkpoints.load_state(run_id)
            pending = unmatched_tool_calls(state.messages)
            tool_name = pending[0].name if pending else ""
            reason = stored.final_message or "Tool approval required"
            return AttachOutcome(
                run_id=run_id,
                kind="approval_pending",
                tool_name=tool_name,
                approval_reason=reason,
            )

        if advance_pending:
            return AttachOutcome(run_id=run_id, kind="advance")

        return AttachOutcome(run_id=run_id, kind="attached")
