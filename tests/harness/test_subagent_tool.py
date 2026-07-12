from __future__ import annotations

from pathlib import Path

import pytest

from milky_frog.domain import RunCancellation, RunResult, RunStatus
from milky_frog.harness.tools import ToolContext
from milky_frog.harness.tools.builtins import read_only_tools
from milky_frog.harness.tools.builtins.subagent import SubagentInput, SubagentTool


class RecordingRunner:
    """Stub ``SubagentRunner`` that records its call and returns a fixed result."""

    def __init__(self, result: RunResult) -> None:
        self.result = result
        self.calls: list[tuple[str, int | None, RunCancellation | None, str]] = []

    async def __call__(
        self,
        prompt: str,
        max_model_calls: int | None,
        cancellation: RunCancellation | None,
        parent_run_id: str,
    ) -> RunResult:
        self.calls.append((prompt, max_model_calls, cancellation, parent_run_id))
        return self.result


@pytest.mark.asyncio
async def test_execute_maps_completed_result_to_success() -> None:
    runner = RecordingRunner(
        RunResult(
            run_id="nested-1", status=RunStatus.COMPLETED, final_message="report", model_calls=2
        )
    )
    tool = SubagentTool(runner)
    context = ToolContext("run-1", Path("."))

    result = await tool.execute(context, SubagentInput(prompt="investigate X"))

    assert result.content == "report"
    assert result.is_error is False
    assert runner.calls == [("investigate X", None, None, "run-1")]


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
    context = ToolContext("run-1", Path("."), cancellation)

    await tool.execute(context, SubagentInput(prompt="go", max_model_calls=5))

    assert runner.calls == [("go", 5, cancellation, "run-1")]


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
