from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from milky_frog.domain import FollowUpCall, ToolResult
from milky_frog.harness.subagent_worktree import (
    MergeConflictError,
    SubagentWorktreeError,
    merge_and_remove_worktree,
)
from milky_frog.harness.tools.base import ToolContext


class MergeWorktreeInput(BaseModel):
    worktree: str = Field(description="Absolute path to the subagent's worktree.")
    branch: str = Field(description="The subagent's branch to merge into the current HEAD.")


class MergeWorktreeTool:
    """Merge a ``subagent`` (``write`` capability) worktree branch back into the workspace.

    Never called directly by the model: the ``subagent`` Tool sets
    ``ToolResult.follow_up`` when it leaves a dirty worktree behind, and
    ``AgentLoop.advance`` synthesizes this call itself, gated by
    ``requires_approval`` like any other Tool call that needs a human. This
    keeps "should this be merged" a deterministic pause instead of hoping the
    model raises it in conversation.
    """

    name = "merge_worktree"
    requires_approval = True
    description = (
        "Merge a subagent's isolated git worktree branch into the current workspace and "
        "remove the worktree. Only invoked as a harness-synthesized follow-up to a "
        "subagent call that left uncommitted work; never call this directly."
    )
    input_model: type[BaseModel] = MergeWorktreeInput

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        params = MergeWorktreeInput.model_validate(input)
        sandbox = context.require_sandbox()
        try:
            await merge_and_remove_worktree(sandbox, Path(params.worktree), params.branch)
        except MergeConflictError as error:
            # A real content conflict, not a plumbing failure: deterministically
            # offer to delegate resolution to a fresh write-capability subagent
            # (an "integrator") instead of only reporting the conflict and
            # hoping the model thinks to suggest that itself. Reuses the exact
            # same follow-up → NEEDS_APPROVAL pause as the merge confirmation
            # itself — no new mechanism, just a second producer of follow_up.
            return ToolResult(
                str(error),
                is_error=True,
                follow_up=FollowUpCall(
                    tool_name="subagent",
                    arguments={
                        "prompt": (
                            f"A merge conflict occurred merging branch '{error.branch}' into "
                            "the current HEAD. The original subagent's worktree is preserved "
                            f"at '{error.worktree}' for reference. Investigate the conflicting "
                            f"changes (e.g. `git diff HEAD {error.branch}`, or inspect "
                            f"'{error.worktree}' directly), resolve them in your own workspace, "
                            "and commit the resolution."
                        ),
                        "capability": "write",
                    },
                ),
            )
        except SubagentWorktreeError as error:
            return ToolResult(str(error), is_error=True)
        return ToolResult(f"merged {params.branch} into the workspace and removed the worktree")
