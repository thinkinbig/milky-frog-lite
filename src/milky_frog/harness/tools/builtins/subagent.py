from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from milky_frog.domain import FollowUpCall, RunCancellation, RunResult, RunStatus, ToolResult
from milky_frog.harness.tools.base import ToolContext


class SubagentInput(BaseModel):
    prompt: str = Field(description="The task to hand off to the nested Run.")
    capability: Literal["read_only", "write"] = Field(
        default="read_only",
        description=(
            "read_only researches in the parent Workspace; write uses a dedicated git "
            "worktree and requires the Docker Sandbox."
        ),
    )
    max_model_calls: int | None = Field(
        default=None,
        description="Model-call budget for the nested Run. Defaults to the harness default.",
    )


@dataclass(frozen=True, slots=True)
class SubagentOutcome:
    result: RunResult
    worktree: Path | None = None
    branch: str | None = None
    worktree_kept: bool = False


class SubagentRejected(RuntimeError):
    """The requested capability cannot be provided safely."""


class SubagentRunner(Protocol):
    """Runs a nested, read-only Run against the parent's workspace.

    Owned and constructed by ``AgentSession`` (the only place holding every
    ingredient — model, hub, checkpoints, sandbox_factory — needed to build a
    second ``AgentHarness``), then injected into ``SubagentTool``.
    """

    async def __call__(
        self,
        prompt: str,
        capability: Literal["read_only", "write"],
        max_model_calls: int | None,
        cancellation: RunCancellation | None,
        workspace: Path,
    ) -> SubagentOutcome: ...


class SubagentTool:
    """Delegate a sub-task to a nested Run limited to read-only Tools.

    Blocks until the nested Run finishes (synchronous hand-off, not a
    fire-and-forget background task). The nested Run's own ``ToolRegistry``
    never includes ``subagent`` itself, so nesting is capped at one level by
    construction.

    Requires approval at the boundary: the nested Run auto-approves its own
    read-only Tools (it has no UI to resolve a pause — see ``AgentSession``),
    and that set includes network-egress Tools (``fetch``/``web_search``) that
    require approval at the top level. Gating ``subagent`` itself keeps a human
    in the loop before any nested capability runs, so delegation cannot be used
    to reach the network without a prompt. Its ``prompt`` argument surfaces in
    the approval message, so the human sees what is being delegated.
    """

    name = "subagent"
    requires_approval = True
    description = (
        "Delegate a sub-task to a nested Run. capability='read_only' provides read_file, "
        "grep, list_dir, fetch, and web_search in the parent Workspace. capability='write' "
        "provides all built-in Tools in an isolated git worktree and requires "
        "[sandbox].kind='docker'. Blocks until completion and cannot recurse."
    )
    input_model: type[BaseModel] = SubagentInput

    def __init__(self, runner: SubagentRunner) -> None:
        self._runner = runner

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        params = SubagentInput.model_validate(input)
        try:
            outcome = await self._runner(
                params.prompt,
                params.capability,
                params.max_model_calls,
                context.cancellation,
                context.workspace,
            )
        except SubagentRejected as error:
            return ToolResult(str(error), is_error=True)
        result = outcome.result
        content = result.final_message
        follow_up = None
        if outcome.worktree is not None and outcome.branch is not None:
            if outcome.worktree_kept:
                header = (
                    f"Subagent finished (run_id={result.run_id}, "
                    f"worktree={outcome.worktree}, branch={outcome.branch})"
                )
                # Deterministically pause the Run for a merge decision instead of
                # relying on the model to raise it — see AgentLoop.advance.
                follow_up = FollowUpCall(
                    tool_name="merge_worktree",
                    arguments={"worktree": str(outcome.worktree), "branch": outcome.branch},
                )
            else:
                header = f"Subagent finished (run_id={result.run_id}; clean worktree removed)"
            content = f"{header}\n{content}"
        return ToolResult(
            content=content,
            is_error=result.status is not RunStatus.COMPLETED,
            follow_up=follow_up,
        )
