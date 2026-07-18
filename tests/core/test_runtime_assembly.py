from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic import BaseModel

from milky_frog.adapters.local import LocalSandbox
from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.core.runtime.assemble import (
    HarnessAssembly,
    make_harness_runtime,
)
from milky_frog.core.runtime.subagent import SubagentRuntime
from milky_frog.core.sandbox import CommandResult
from milky_frog.domain import (
    ModelChunk,
    ModelRequest,
    ModelResponse,
    RunRequest,
    StreamDone,
    ToolCall,
    ToolDecision,
)
from milky_frog.events import EventHub
from milky_frog.handlers.checkpoint import CheckpointHandler
from milky_frog.harness.subagent_worktree import merge_and_remove_worktree
from milky_frog.harness.tools import ToolContext, ToolResult
from milky_frog.harness.tools.builtins.subagent import SubagentInput


class ReadOnlyRecordingModel:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        self.requests.append(request)
        yield StreamDone(ModelResponse(content="nested report"))


class BlockingModel:
    def __init__(self, *, write_first: bool) -> None:
        self.write_first = write_first
        self.calls = 0
        self.blocked = asyncio.Event()

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        self.calls += 1
        if self.write_first and self.calls == 1:
            yield StreamDone(
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            "write-1",
                            "write_file",
                            {"path": "feature.txt", "content": "preserve me"},
                        ),
                    )
                )
            )
            return
        self.blocked.set()
        await asyncio.Future()


class SlowCloseSandboxFactory:
    def __init__(self) -> None:
        self.acquired = False
        self.closing = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False

    def __call__(self, workspace: Path) -> LocalSandbox:
        self.acquired = True
        return LocalSandbox(workspace)

    async def aclose(self) -> None:
        self.closing.set()
        await self.release.wait()
        self.closed = True


class McpInput(BaseModel):
    pass


class FirstMcpTool:
    name = "first_mcp"
    description = "First MCP Tool"
    input_model: type[BaseModel] = McpInput

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        del context, input
        return ToolResult("first")


class ReplacementMcpTool:
    name = "replacement_mcp"
    description = "Replacement MCP Tool"
    input_model: type[BaseModel] = McpInput

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        del context, input
        return ToolResult("replacement")


def _assembly(
    model: ReadOnlyRecordingModel | BlockingModel,
    store: SqliteCheckpointStore,
    hub: EventHub,
) -> HarnessAssembly:
    CheckpointHandler(store).register(hub)
    return HarnessAssembly(model=model, checkpoints=store, hub=hub)


async def _init_container_workspace(workspace: Path) -> LocalSandbox:
    config_dir = workspace / ".milky-frog"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        '[sandbox]\nkind = "docker"\nimage = "python:3.12"\n',
        encoding="utf-8",
    )
    sandbox = LocalSandbox(workspace)
    initialized = await sandbox.run_command(
        "git init && git add .milky-frog/config.toml && "
        "git -c user.name=test -c user.email=test@example.com commit -m init",
        timeout_seconds=10,
    )
    assert isinstance(initialized, CommandResult)
    assert initialized.exit_code == 0, initialized.output
    return sandbox


async def _worktree_records(sandbox: LocalSandbox) -> list[dict[str, str]]:
    listed = await sandbox.run_command("git worktree list --porcelain", timeout_seconds=10)
    assert isinstance(listed, CommandResult)
    assert listed.exit_code == 0, listed.output
    records: list[dict[str, str]] = []
    for block in listed.output.strip().split("\n\n"):
        record: dict[str, str] = {}
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            record[key] = value
        records.append(record)
    return records


@pytest.mark.asyncio
async def test_harness_runtime_runs_read_only_nested_run(tmp_path: Path) -> None:
    model = ReadOnlyRecordingModel()
    store = SqliteCheckpointStore(tmp_path / "state.db")
    runtime = make_harness_runtime(_assembly(model, store, EventHub()))

    subagent = runtime.registry.get("subagent")
    tool_result = await subagent.execute(
        ToolContext("parent-1", tmp_path),
        SubagentInput(prompt="investigate", max_model_calls=3),
    )

    assert tool_result.is_error is False
    assert tool_result.content == "nested report"
    tool_names = {schema["function"]["name"] for schema in model.requests[0].tools}
    assert tool_names == {"read_file", "list_dir", "grep", "fetch"}
    foreground_names = {tool.name for tool in runtime.registry.tools()}
    assert "subagent" in foreground_names
    assert "write_file" in foreground_names
    assert (
        runtime.foreground.policy.decide(
            ToolCall("delegate-1", "subagent", {"prompt": "investigate"})
        )
        is ToolDecision.NEEDS_APPROVAL
    )

    nested_run = next(run for run in store.list_runs() if run.workspace == tmp_path.resolve())
    nested_state = store.load_state(nested_run.run_id)
    assert nested_state.run_kind == "subagent"
    assert nested_state.parent_run_id == "parent-1"


@pytest.mark.asyncio
async def test_mcp_replacement_preserves_subagent_and_updates_foreground_schema(
    tmp_path: Path,
) -> None:
    model = ReadOnlyRecordingModel()
    store = SqliteCheckpointStore(tmp_path / "state.db")
    runtime = make_harness_runtime(_assembly(model, store, EventHub()))

    assert runtime.registry.replace_mcp_tools((FirstMcpTool(),)) == 1
    assert runtime.registry.replace_mcp_tools((ReplacementMcpTool(),)) == 1
    await runtime.foreground.run(RunRequest("inspect", tmp_path, max_model_calls=1))

    tool_names = {schema["function"]["name"] for schema in model.requests[0].tools}
    assert "subagent" in tool_names
    assert "replacement_mcp" in tool_names
    assert "first_mcp" not in tool_names


@pytest.mark.asyncio
async def test_cancelled_write_nested_run_removes_clean_worktree(tmp_path: Path) -> None:
    sandbox = await _init_container_workspace(tmp_path)
    model = BlockingModel(write_first=False)
    store = SqliteCheckpointStore(tmp_path / "state.db")
    runner = SubagentRuntime(_assembly(model, store, EventHub()))

    task = asyncio.create_task(runner("wait", "write", None, None, tmp_path, "parent-1"))
    await asyncio.wait_for(model.blocked.wait(), timeout=3)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    records = await _worktree_records(sandbox)
    assert [Path(record["worktree"]) for record in records] == [tmp_path.resolve()]


@pytest.mark.asyncio
async def test_cancelled_write_nested_run_preserves_and_merges_work(
    tmp_path: Path,
) -> None:
    sandbox = await _init_container_workspace(tmp_path)
    model = BlockingModel(write_first=True)
    store = SqliteCheckpointStore(tmp_path / "state.db")
    runner = SubagentRuntime(_assembly(model, store, EventHub()))

    task = asyncio.create_task(runner("write then wait", "write", None, None, tmp_path, "parent-1"))
    await asyncio.wait_for(model.blocked.wait(), timeout=3)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    records = await _worktree_records(sandbox)
    nested = next(record for record in records if Path(record["worktree"]) != tmp_path.resolve())
    worktree = Path(nested["worktree"])
    branch = nested["branch"].removeprefix("refs/heads/")
    assert (worktree / "feature.txt").read_text(encoding="utf-8") == "preserve me"
    assert not (tmp_path / "feature.txt").exists()

    await merge_and_remove_worktree(sandbox, worktree, branch)

    assert (tmp_path / "feature.txt").read_text(encoding="utf-8") == "preserve me"
    assert not worktree.exists()


@pytest.mark.asyncio
async def test_repeated_cancellation_waits_for_sandbox_close_and_worktree_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = await _init_container_workspace(tmp_path)
    model = BlockingModel(write_first=False)
    store = SqliteCheckpointStore(tmp_path / "state.db")
    runner = SubagentRuntime(_assembly(model, store, EventHub()))
    slow_factory = SlowCloseSandboxFactory()

    def make_slow_factory(
        config: object,
        worktree: object,
    ) -> SlowCloseSandboxFactory:
        del config, worktree
        return slow_factory

    monkeypatch.setattr(
        SubagentRuntime,
        "_make_worktree_sandbox",
        staticmethod(make_slow_factory),
    )

    task = asyncio.create_task(runner("wait", "write", None, None, tmp_path, "parent-1"))
    await asyncio.wait_for(model.blocked.wait(), timeout=3)
    assert slow_factory.acquired is True
    task.cancel()
    await asyncio.wait_for(slow_factory.closing.wait(), timeout=3)

    task.cancel()
    await asyncio.sleep(0)
    assert task.done() is False
    slow_factory.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert slow_factory.closed is True
    records = await _worktree_records(sandbox)
    assert [Path(record["worktree"]) for record in records] == [tmp_path.resolve()]
