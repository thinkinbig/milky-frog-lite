from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from milky_frog.domain import RunCancellation, RunResult, RunStatus, ToolResult
from milky_frog.harness.tools.base import ToolContext


class SubagentInput(BaseModel):
    prompt: str = Field(description="The task to hand off to the nested Run.")
    max_model_calls: int | None = Field(
        default=None,
        description="Model-call budget for the nested Run. Defaults to the harness default.",
    )


class SubagentRunner(Protocol):
    """Runs a nested, read-only Run against the parent's workspace.

    Owned and constructed by ``AgentSession`` (the only place holding every
    ingredient — model, hub, checkpoints, sandbox_factory — needed to build a
    second ``AgentHarness``), then injected into ``SubagentTool``.
    """

    async def __call__(
        self,
        prompt: str,
        max_model_calls: int | None,
        cancellation: RunCancellation | None,
        parent_run_id: str,
    ) -> RunResult: ...


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
        "Delegate a sub-task (research, investigation, summarization) to a nested Run "
        "limited to read-only Tools (read_file, grep, list_dir, fetch, web_search). "
        "Blocks until the sub-task finishes and returns its final report. Cannot write "
        "files or run shell commands, and cannot itself spawn another subagent."
    )
    input_model: type[BaseModel] = SubagentInput

    def __init__(self, runner: SubagentRunner) -> None:
        self._runner = runner

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        params = SubagentInput.model_validate(input)
        result = await self._runner(
            params.prompt, params.max_model_calls, context.cancellation, context.run_id
        )
        return ToolResult(
            content=result.final_message,
            is_error=result.status is not RunStatus.COMPLETED,
        )
