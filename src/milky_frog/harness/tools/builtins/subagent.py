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
    ) -> RunResult: ...


class SubagentTool:
    """Delegate a sub-task to a nested Run limited to read-only Tools.

    Blocks until the nested Run finishes (synchronous hand-off, not a
    fire-and-forget background task). The nested Run's own ``ToolRegistry``
    never includes ``subagent`` itself, so nesting is capped at one level by
    construction.
    """

    name = "subagent"
    requires_approval = False
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
        result = await self._runner(params.prompt, params.max_model_calls, context.cancellation)
        return ToolResult(
            content=result.final_message,
            is_error=result.status is not RunStatus.COMPLETED,
        )
