from __future__ import annotations

from pathlib import Path

import pytest

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.domain import RunResult, RunState, RunStatus, ToolCall
from milky_frog.events import (
    EventHub,
    RunCancelled,
    RunFailed,
    RunNotice,
    RunTurnEnd,
    RunTurnStart,
)
from milky_frog.handlers.checkpoint import CheckpointHandler


def _make_dispatcher(store: SqliteCheckpointStore, registry: EventHub) -> EventHub:
    CheckpointHandler(store).register(registry)
    return registry


@pytest.mark.asyncio
async def test_run_cancelled_persists_checkpoint_before_handler(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    registry = EventHub()
    checkpoint_seen = False

    @registry.on(RunCancelled)
    async def record(_event: RunCancelled, _ctx=None) -> None:
        nonlocal checkpoint_seen
        run = store.get_run(_event.run_id)
        checkpoint_seen = run is not None and run.status is RunStatus.CANCELLED

    dispatcher = _make_dispatcher(store, registry)
    state = RunState(run_id="run-1", workspace=tmp_path)
    store.create_run(state.run_id, tmp_path)

    await dispatcher.run_cancelled(
        state,
        RunResult(state.run_id, RunStatus.CANCELLED, "cancelled", 0),
    )

    assert checkpoint_seen is True


@pytest.mark.asyncio
async def test_run_failed_persists_checkpoint_before_handler(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    registry = EventHub()
    checkpoint_seen = False

    @registry.on(RunFailed)
    async def record(_event: RunFailed, _ctx=None) -> None:
        nonlocal checkpoint_seen
        run = store.get_run(_event.run_id)
        checkpoint_seen = run is not None and run.status is RunStatus.FAILED

    dispatcher = _make_dispatcher(store, registry)
    state = RunState(run_id="run-2", workspace=tmp_path)
    store.create_run(state.run_id, tmp_path)

    await dispatcher.run_failed(
        state,
        RunResult(state.run_id, RunStatus.FAILED, "RuntimeError: boom", 0),
    )

    assert checkpoint_seen is True


@pytest.mark.asyncio
async def test_finish_failed_returns_result_and_notifies(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    registry = EventHub()
    failed: list[RunFailed] = []

    @registry.on(RunFailed)
    async def record(event: RunFailed, _ctx=None) -> None:
        failed.append(event)

    dispatcher = _make_dispatcher(store, registry)
    state = RunState(run_id="run-3", workspace=tmp_path)
    store.create_run(state.run_id, tmp_path)

    result = await dispatcher.finish_failed(state, RuntimeError("boom"))

    assert result.status is RunStatus.FAILED
    assert result.final_message == "RuntimeError: boom"
    assert len(failed) == 1
    assert failed[0].result is result


@pytest.mark.asyncio
async def test_turn_started_notifies_handler(tmp_path: Path) -> None:
    registry = EventHub()
    seen: list[RunTurnStart] = []

    @registry.on(RunTurnStart)
    async def record(event: RunTurnStart, _ctx=None) -> None:
        seen.append(event)

    dispatcher = registry
    await dispatcher.turn_started("run-1", model_call=3)

    assert len(seen) == 1
    assert seen[0].run_id == "run-1"
    assert seen[0].model_call == 3


@pytest.mark.asyncio
async def test_turn_ended_notifies_handler(tmp_path: Path) -> None:
    registry = EventHub()
    seen: list[RunTurnEnd] = []

    @registry.on(RunTurnEnd)
    async def record(event: RunTurnEnd, _ctx=None) -> None:
        seen.append(event)

    dispatcher = registry
    await dispatcher.turn_ended("run-1", model_call=2)

    assert len(seen) == 1
    assert seen[0].run_id == "run-1"
    assert seen[0].model_call == 2


@pytest.mark.asyncio
async def test_run_notice_notifies_handler() -> None:
    registry = EventHub()
    seen: list[RunNotice] = []

    @registry.on(RunNotice)
    async def record(event: RunNotice, _ctx=None) -> None:
        seen.append(event)

    dispatcher = registry
    await dispatcher.run_notice("run-1", "retrying model connection", level="warning")

    assert len(seen) == 1
    assert seen[0].message == "retrying model connection"
    assert seen[0].level == "warning"


# ── Additional lifecycle event tests ─────────────────────────────────────


@pytest.mark.asyncio
async def test_run_before_start_collects_system_prompt_sections(
    tmp_path: Path,
) -> None:
    from milky_frog.domain import RunRequest
    from milky_frog.events.events import RunBeforeStart
    from milky_frog.handlers.context import SystemPromptSection

    registry = EventHub()
    dispatcher = registry

    @registry.on(RunBeforeStart)
    async def inject_skill(event: RunBeforeStart, _ctx=None) -> SystemPromptSection | None:
        if event.workspace == tmp_path:
            return SystemPromptSection(content="custom skill content")
        return None

    sections = await dispatcher.run_before_start(
        "run-1",
        RunRequest(prompt="hi", workspace=tmp_path),
        tmp_path,
    )

    assert "custom skill content" in sections


@pytest.mark.asyncio
async def test_run_started_notifies_handlers(tmp_path: Path) -> None:
    from milky_frog.domain import RunRequest, RunState
    from milky_frog.events.events import RunStarted

    registry = EventHub()
    dispatcher = registry
    seen: list[RunStarted] = []

    @registry.on(RunStarted)
    async def record(event: RunStarted, _ctx=None) -> None:
        seen.append(event)

    state = RunState(run_id="run-1", workspace=tmp_path)
    request = RunRequest(prompt="hi", workspace=tmp_path)
    await dispatcher.run_started("run-1", request, state)

    assert len(seen) == 1
    assert seen[0].run_id == "run-1"
    assert seen[0].request.prompt == "hi"
    assert seen[0].state.workspace == tmp_path


@pytest.mark.asyncio
async def test_before_resume_notifies_handlers(tmp_path: Path) -> None:
    from milky_frog.domain import RunStatus
    from milky_frog.events.events import RunBeforeResume

    registry = EventHub()
    dispatcher = registry
    seen: list[RunBeforeResume] = []

    @registry.on(RunBeforeResume)
    async def record(event: RunBeforeResume, _ctx=None) -> None:
        seen.append(event)

    await dispatcher.before_resume(
        "run-1", prompt="continue", status=RunStatus.PAUSED_LIMIT, workspace=tmp_path
    )

    assert len(seen) == 1
    assert seen[0].run_id == "run-1"
    assert seen[0].prompt == "continue"
    assert seen[0].stored_status is RunStatus.PAUSED_LIMIT
    assert seen[0].workspace == tmp_path


@pytest.mark.asyncio
async def test_before_model_notifies_handlers() -> None:
    from milky_frog.domain import Message, MessageRole, ModelRequest
    from milky_frog.events.events import RunBeforeModel

    registry = EventHub()
    dispatcher = registry
    seen: list[RunBeforeModel] = []

    @registry.on(RunBeforeModel)
    async def record(event: RunBeforeModel, _ctx=None) -> None:
        seen.append(event)

    request = ModelRequest(
        messages=(Message(role=MessageRole.USER, content="hi"),),
        tools=(),
    )
    await dispatcher.before_model("run-1", request)

    assert len(seen) == 1
    assert seen[0].run_id == "run-1"
    assert seen[0].request.messages[0].content == "hi"


@pytest.mark.asyncio
async def test_on_model_chunk_notifies_handlers() -> None:
    from milky_frog.domain import Message, MessageRole, ModelRequest, TextDelta
    from milky_frog.events.events import RunModelChunk

    registry = EventHub()
    dispatcher = registry
    seen: list[RunModelChunk] = []

    @registry.on(RunModelChunk)
    async def record(event: RunModelChunk, _ctx=None) -> None:
        seen.append(event)

    request = ModelRequest(
        messages=(Message(role=MessageRole.USER, content="hi"),),
        tools=(),
    )
    await dispatcher.on_model_chunk("run-1", request, TextDelta("hello"))

    assert len(seen) == 1
    assert seen[0].chunk.content == "hello"


@pytest.mark.asyncio
async def test_on_model_reasoning_notifies_handlers() -> None:
    from milky_frog.domain import Message, MessageRole, ModelRequest, ReasoningDelta
    from milky_frog.events.events import RunModelReasoning

    registry = EventHub()
    dispatcher = registry
    seen: list[RunModelReasoning] = []

    @registry.on(RunModelReasoning)
    async def record(event: RunModelReasoning, _ctx=None) -> None:
        seen.append(event)

    request = ModelRequest(
        messages=(Message(role=MessageRole.USER, content="hi"),),
        tools=(),
    )
    await dispatcher.on_model_reasoning("run-1", request, ReasoningDelta("thinking..."))

    assert len(seen) == 1
    assert seen[0].chunk.content == "thinking..."


@pytest.mark.asyncio
async def test_after_model_notifies_handlers(tmp_path: Path) -> None:
    from milky_frog.domain import (
        Message,
        MessageRole,
        ModelRequest,
        ModelResponse,
        RunState,
        TokenUsage,
    )
    from milky_frog.events.events import RunAfterModel

    registry = EventHub()
    dispatcher = registry
    seen: list[RunAfterModel] = []

    @registry.on(RunAfterModel)
    async def record(event: RunAfterModel, _ctx=None) -> None:
        seen.append(event)

    request = ModelRequest(
        messages=(Message(role=MessageRole.USER, content="hi"),),
        tools=(),
    )
    response = ModelResponse(content="hello", usage=TokenUsage(input_tokens=10, output_tokens=5))
    state = RunState(run_id="run-1", workspace=tmp_path)
    await dispatcher.after_model("run-1", request, response, state)

    assert len(seen) == 1
    assert seen[0].response.content == "hello"
    assert seen[0].state.run_id == "run-1"


@pytest.mark.asyncio
async def test_before_tool_notifies_handlers() -> None:
    from milky_frog.events.events import RunBeforeTool

    registry = EventHub()
    dispatcher = registry
    seen: list[RunBeforeTool] = []

    @registry.on(RunBeforeTool)
    async def record(event: RunBeforeTool, _ctx=None) -> None:
        seen.append(event)

    call = ToolCall("call-1", "echo", {"text": "hello"})
    await dispatcher.before_tool("run-1", call)

    assert len(seen) == 1
    assert seen[0].call.name == "echo"


@pytest.mark.asyncio
async def test_after_tool_notifies_handlers(tmp_path: Path) -> None:
    from milky_frog.domain import RunState, ToolResult
    from milky_frog.events.events import RunAfterTool

    registry = EventHub()
    dispatcher = registry
    seen: list[RunAfterTool] = []

    @registry.on(RunAfterTool)
    async def record(event: RunAfterTool, _ctx=None) -> None:
        seen.append(event)

    call = ToolCall("call-1", "echo", {"text": "hello"})
    result = ToolResult("hello back")
    state = RunState(run_id="run-1", workspace=tmp_path)
    await dispatcher.after_tool("run-1", call, result, state)

    assert len(seen) == 1
    assert seen[0].call.name == "echo"
    assert seen[0].result.content == "hello back"
    assert seen[0].state.run_id == "run-1"


@pytest.mark.asyncio
async def test_finish_completed_returns_result_and_notifies(tmp_path: Path) -> None:
    from milky_frog.domain import RunStatus
    from milky_frog.events.events import RunCompleted

    registry = EventHub()
    dispatcher = registry
    seen: list[RunCompleted] = []

    @registry.on(RunCompleted)
    async def record(event: RunCompleted, _ctx=None) -> None:
        seen.append(event)

    state = RunState(run_id="run-1", workspace=tmp_path, completed_model_calls=3)
    result = await dispatcher.finish_completed(state, "all done")

    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "all done"
    assert result.model_calls == 3
    assert len(seen) == 1
    assert seen[0].result.status is RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_finish_paused_returns_result_and_notifies(tmp_path: Path) -> None:
    from milky_frog.domain import RunStatus
    from milky_frog.events.events import RunPaused

    registry = EventHub()
    dispatcher = registry
    seen: list[RunPaused] = []

    @registry.on(RunPaused)
    async def record(event: RunPaused, _ctx=None) -> None:
        seen.append(event)

    state = RunState(run_id="run-1", workspace=tmp_path, completed_model_calls=5)
    result = await dispatcher.finish_paused(state, max_model_calls=10)

    assert result.status is RunStatus.PAUSED_LIMIT
    assert "model call limit" in result.final_message
    assert result.model_calls == 5
    assert len(seen) == 1
    assert seen[0].result.status is RunStatus.PAUSED_LIMIT


@pytest.mark.asyncio
async def test_finish_cancelled_returns_result_and_notifies(tmp_path: Path) -> None:
    from milky_frog.domain import RunStatus
    from milky_frog.events.events import RunCancelled

    registry = EventHub()
    dispatcher = registry
    seen: list[RunCancelled] = []

    @registry.on(RunCancelled)
    async def record(event: RunCancelled, _ctx=None) -> None:
        seen.append(event)

    state = RunState(run_id="run-1", workspace=tmp_path, completed_model_calls=2)
    result = await dispatcher.finish_cancelled(state, reason="user cancelled")

    assert result.status is RunStatus.CANCELLED
    assert result.final_message == "user cancelled"
    assert result.model_calls == 2
    assert len(seen) == 1
    assert seen[0].result.status is RunStatus.CANCELLED


@pytest.mark.asyncio
async def test_finish_approval_needed_returns_result_and_notifies(tmp_path: Path) -> None:
    from milky_frog.domain import RunStatus
    from milky_frog.events.events import RunPaused

    registry = EventHub()
    dispatcher = registry
    seen: list[RunPaused] = []

    @registry.on(RunPaused)
    async def record(event: RunPaused, _ctx=None) -> None:
        seen.append(event)

    state = RunState(run_id="run-1", workspace=tmp_path, completed_model_calls=1)
    call = ToolCall("call-1", "write", {"path": "main.py"})
    result = await dispatcher.finish_approval_needed(state, call)

    assert result.status is RunStatus.WAITING_FOR_APPROVAL
    assert "write" in result.final_message
    assert "main.py" in result.final_message
    assert result.model_calls == 1
    assert len(seen) == 1
    assert seen[0].result.status is RunStatus.WAITING_FOR_APPROVAL


@pytest.mark.asyncio
async def test_finish_approval_needed_format_for_bash(tmp_path: Path) -> None:
    state = RunState(run_id="run-1", workspace=tmp_path)
    dispatcher = EventHub()

    call = ToolCall("call-1", "bash", {"command": "rm -rf /"})
    result = await dispatcher.finish_approval_needed(state, call)

    assert result.status is RunStatus.WAITING_FOR_APPROVAL
    assert "bash" in result.final_message
    assert "rm -rf" in result.final_message


@pytest.mark.asyncio
async def test_finish_approval_needed_format_for_bash_no_command(
    tmp_path: Path,
) -> None:
    state = RunState(run_id="run-1", workspace=tmp_path)
    dispatcher = EventHub()

    call = ToolCall("call-1", "bash", {})
    result = await dispatcher.finish_approval_needed(state, call)

    assert result.status is RunStatus.WAITING_FOR_APPROVAL
    assert "bash" in result.final_message


@pytest.mark.asyncio
async def test_finish_approval_needed_format_for_read_with_path(
    tmp_path: Path,
) -> None:
    state = RunState(run_id="run-1", workspace=tmp_path)
    dispatcher = EventHub()

    call = ToolCall("call-1", "read", {"path": "secret.txt"})
    result = await dispatcher.finish_approval_needed(state, call)

    assert result.status is RunStatus.WAITING_FOR_APPROVAL
    assert "read" in result.final_message
    assert "secret.txt" in result.final_message


@pytest.mark.asyncio
async def test_finish_approval_needed_format_for_read_no_path(
    tmp_path: Path,
) -> None:
    state = RunState(run_id="run-1", workspace=tmp_path)
    dispatcher = EventHub()

    call = ToolCall("call-1", "read", {})
    result = await dispatcher.finish_approval_needed(state, call)

    assert result.status is RunStatus.WAITING_FOR_APPROVAL
    assert "read" in result.final_message


@pytest.mark.asyncio
async def test_finish_approval_needed_format_for_write_no_path(
    tmp_path: Path,
) -> None:
    state = RunState(run_id="run-1", workspace=tmp_path)
    dispatcher = EventHub()

    call = ToolCall("call-1", "write", {})
    result = await dispatcher.finish_approval_needed(state, call)

    assert result.status is RunStatus.WAITING_FOR_APPROVAL
    assert "write" in result.final_message


@pytest.mark.asyncio
async def test_finish_approval_needed_format_for_edit_with_path(
    tmp_path: Path,
) -> None:
    state = RunState(run_id="run-1", workspace=tmp_path)
    dispatcher = EventHub()

    call = ToolCall("call-1", "edit", {"path": "main.py"})
    result = await dispatcher.finish_approval_needed(state, call)

    assert result.status is RunStatus.WAITING_FOR_APPROVAL
    assert "edit" in result.final_message
    assert "main.py" in result.final_message


@pytest.mark.asyncio
async def test_finish_approval_needed_format_for_edit_no_path(
    tmp_path: Path,
) -> None:
    state = RunState(run_id="run-1", workspace=tmp_path)
    dispatcher = EventHub()

    call = ToolCall("call-1", "edit", {})
    result = await dispatcher.finish_approval_needed(state, call)

    assert result.status is RunStatus.WAITING_FOR_APPROVAL
    assert "edit" in result.final_message


@pytest.mark.asyncio
async def test_finish_approval_needed_format_for_generic_tool_with_preview(
    tmp_path: Path,
) -> None:
    state = RunState(run_id="run-1", workspace=tmp_path)
    dispatcher = EventHub()

    call = ToolCall("call-1", "my_tool", {"pattern": "def foo", "path": "src/"})
    result = await dispatcher.finish_approval_needed(state, call)

    assert result.status is RunStatus.WAITING_FOR_APPROVAL
    assert "my_tool" in result.final_message
    assert "pattern" in result.final_message


@pytest.mark.asyncio
async def test_finish_approval_needed_format_for_generic_tool_no_preview(
    tmp_path: Path,
) -> None:
    state = RunState(run_id="run-1", workspace=tmp_path)
    dispatcher = EventHub()

    call = ToolCall("call-1", "my_tool", {"count": 42})
    result = await dispatcher.finish_approval_needed(state, call)

    assert result.status is RunStatus.WAITING_FOR_APPROVAL
    assert "my_tool" in result.final_message
    assert "Allow this call" in result.final_message
