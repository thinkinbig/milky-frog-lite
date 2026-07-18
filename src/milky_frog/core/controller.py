from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from milky_frog.checkpoint import StoredRun
from milky_frog.core.runtime.checkpoint import RunCheckpointFacade
from milky_frog.domain import RunStatus, ToolCall
from milky_frog.harness.state import unmatched_tool_calls


@dataclass(frozen=True, slots=True)
class AttachOutcome:
    """What the UI should do after attaching to a Run."""

    run_id: str
    kind: str  # "prompt_continue" | "approval_pending" | "advance" | "attached"
    pending_calls: tuple[ToolCall, ...] = ()


class RunController:
    """Foreground Run control: attach, checkpoint reads, and workspace listing.

    Keeps resume/attach logic out of the Textual presentation layer.
    """

    def __init__(self, checkpoints: RunCheckpointFacade) -> None:
        self._checkpoints = checkpoints

    def workspace_runs(self, workspace: Path) -> tuple[StoredRun, ...]:
        """Return recent Runs for one Workspace in Checkpoint update order."""
        return self._checkpoints.list_runs(workspace=workspace)

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
            return AttachOutcome(
                run_id=run_id,
                kind="approval_pending",
                pending_calls=unmatched_tool_calls(state.messages),
            )

        if advance_pending:
            return AttachOutcome(run_id=run_id, kind="advance")

        return AttachOutcome(run_id=run_id, kind="attached")
