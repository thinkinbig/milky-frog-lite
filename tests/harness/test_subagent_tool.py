from __future__ import annotations

from pathlib import Path

import pytest

from milky_frog.domain import RunCancellation, RunResult, RunStatus
from milky_frog.harness.tools import ToolContext
from milky_frog.harness.tools.builtins import read_only_tools
from milky_frog.harness.tools.builtins.subagent import (
    SubagentInput,
    SubagentOutcome,
    SubagentTool,
)


class RecordingRunner:
    """Stub ``SubagentRunner`` that records its call and returns a fixed result."""

    def __init__(self, result: RunResult) -> None:
        self.result = result
        self.calls: list[tuple[str, str, int | None, RunCancellation | None, Path, str]] = []

    async def __call__(
        self,
        prompt: str,
        capability: str,
        max_model_calls: int | None,
        cancellation: RunCancellation | None,
        workspace: Path,
        parent_run_id: str,
    ) -> SubagentOutcome:
        self.calls.append(
            (prompt, capability, max_model_calls, cancellation, workspace, parent_run_id)
        )
        return SubagentOutcome(self.result)


@pytest.mark.asyncio
async def test_execute_maps_completed_result_to_success() -> None:
    runner = RecordingRunner(
        RunResult(
            run_id="nested-1", status=RunStatus.COMPLETED, final_message="report", model_calls=2
        )
    )
    tool = SubagentTool(runner)
    workspace = Path(".")
    context = ToolContext("run-1", workspace)

    result = await tool.execute(context, SubagentInput(prompt="investigate X"))

    assert result.content == "report"
    assert result.is_error is False
    assert runner.calls == [("investigate X", "read_only", None, None, workspace, "run-1")]


@pytest.mark.asyncio
async def test_execute_maps_non_completed_result_to_error() -> None:
    runner = RecordingRunner(
        RunResult(run_id="nested-2", status=RunStatus.FAILED, final_message="boom", model_calls=1)
    )
    tool = SubagentTool(runner)
    context = ToolContext("run-1", Path("."))

    result = await tool.execute(context, SubagentInput(prompt="do it"))

    assert result.content == "boom"
    assert result.is_error is True


@pytest.mark.asyncio
async def test_execute_forwards_max_model_calls_and_cancellation() -> None:
    runner = RecordingRunner(
        RunResult(run_id="nested-3", status=RunStatus.COMPLETED, final_message="ok", model_calls=1)
    )
    tool = SubagentTool(runner)
    cancellation = RunCancellation()
    workspace = Path(".")
    context = ToolContext("run-1", workspace, cancellation)

    await tool.execute(context, SubagentInput(prompt="go", max_model_calls=5))

    assert runner.calls == [("go", "read_only", 5, cancellation, workspace, "run-1")]


@pytest.mark.asyncio
async def test_execute_forwards_write_capability_and_reports_worktree(tmp_path: Path) -> None:
    result = RunResult(
        run_id="nested-4", status=RunStatus.COMPLETED, final_message="implemented", model_calls=2
    )

    class WriteRunner(RecordingRunner):
        async def __call__(
            self,
            prompt: str,
            capability: str,
            max_model_calls: int | None,
            cancellation: RunCancellation | None,
            workspace: Path,
            parent_run_id: str,
        ) -> SubagentOutcome:
            self.calls.append(
                (prompt, capability, max_model_calls, cancellation, workspace, parent_run_id)
            )
            return SubagentOutcome(
                self.result,
                worktree=tmp_path / "worktree",
                branch="subagent/nested-4",
                worktree_kept=True,
            )

    runner = WriteRunner(result)
    tool = SubagentTool(runner)
    context = ToolContext("run-1", tmp_path)

    tool_result = await tool.execute(
        context,
        SubagentInput(prompt="implement it", capability="write"),
    )

    assert "worktree=" in tool_result.content
    assert "branch=subagent/nested-4" in tool_result.content
    assert runner.calls == [("implement it", "write", None, None, tmp_path, "run-1")]
    # A kept worktree must deterministically pause the Run for a merge
    # decision — see AgentLoop.advance — not just mention it in the text.
    assert tool_result.follow_up is not None
    assert tool_result.follow_up.tool_name == "merge_worktree"
    assert tool_result.follow_up.arguments == {
        "worktree": str(tmp_path / "worktree"),
        "branch": "subagent/nested-4",
    }


@pytest.mark.asyncio
async def test_execute_sets_no_follow_up_when_worktree_is_clean(tmp_path: Path) -> None:
    result = RunResult(
        run_id="nested-5", status=RunStatus.COMPLETED, final_message="implemented", model_calls=2
    )

    class CleanWriteRunner(RecordingRunner):
        async def __call__(
            self,
            prompt: str,
            capability: str,
            max_model_calls: int | None,
            cancellation: RunCancellation | None,
            workspace: Path,
            parent_run_id: str,
        ) -> SubagentOutcome:
            self.calls.append(
                (prompt, capability, max_model_calls, cancellation, workspace, parent_run_id)
            )
            return SubagentOutcome(
                self.result,
                worktree=tmp_path / "worktree",
                branch="subagent/nested-5",
                worktree_kept=False,
            )

    runner = CleanWriteRunner(result)
    tool = SubagentTool(runner)
    context = ToolContext("run-1", tmp_path)

    tool_result = await tool.execute(
        context,
        SubagentInput(prompt="implement it", capability="write"),
    )

    assert tool_result.follow_up is None


def test_read_only_tools_excludes_write_bash_and_subagent() -> None:
    names = {tool.name for tool in read_only_tools()}

    assert names == {"read_file", "list_dir", "grep", "fetch"}
    assert "write_file" not in names
    assert "edit_file" not in names
    assert "bash" not in names
    assert "subagent" not in names


def test_read_only_tools_includes_web_search_only_with_jina_key() -> None:
    without_key = {tool.name for tool in read_only_tools()}
    with_key = {tool.name for tool in read_only_tools(jina_api_key="jina-key")}

    assert "web_search" not in without_key
    assert "web_search" in with_key
