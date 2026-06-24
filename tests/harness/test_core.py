"""Harness core: model loop, tool execution, streaming, reasoning, identity."""

from __future__ import annotations

from pathlib import Path

import pytest

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.domain import MessageRole, RunRequest, RunStatus, TokenUsage
from milky_frog.handlers import EventDispatcher
from milky_frog.harness.tools import ToolRegistry
from tests.checkpoint_helpers import run_status, tool_messages
from tests.stubs import (
    EarlyStreamDoneModel,
    EchoTool,
    FakeModel,
    IdentityCapturingModel,
    InvalidToolArgsThenRecoverModel,
    ReasoningModel,
    RecordingBackendFactory,
    UsageReportingModel,
    make_harness,
)


@pytest.mark.asyncio
async def test_runs_tool_loop_and_persists_events(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    harness = make_harness(
        model=FakeModel(),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=store,
        handlers=EventDispatcher(),
    )

    result = await harness.run(RunRequest("echo hello", tmp_path))

    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "done"
    assert result.model_calls == 2
    loaded = store.load_state(result.run_id)
    assert [message.role for message in loaded.messages] == [
        MessageRole.SYSTEM,
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    assert run_status(store, result.run_id) is RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_injected_backend_factory_is_used(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    factory = RecordingBackendFactory()
    harness = make_harness(
        model=FakeModel(),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=store,
        handlers=EventDispatcher(),
        backend_factory=factory,
    )

    result = await harness.run(RunRequest("echo hello", tmp_path))

    assert result.status is RunStatus.COMPLETED
    assert factory.calls == [tmp_path.resolve()]


@pytest.mark.asyncio
async def test_invalid_tool_arguments_become_tool_errors(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    harness = make_harness(
        model=InvalidToolArgsThenRecoverModel(),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=store,
        handlers=EventDispatcher(),
    )

    result = await harness.run(RunRequest("echo hello", tmp_path))

    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "recovered"
    assert result.model_calls == 2
    loaded = store.load_state(result.run_id)
    assert run_status(store, result.run_id) is not RunStatus.FAILED
    tool_lines = tool_messages(loaded)
    assert len(tool_lines) == 1
    assert "ValidationError" in tool_lines[0]


@pytest.mark.asyncio
async def test_aggregates_token_usage_across_calls(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    harness = make_harness(
        model=UsageReportingModel(),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=store,
        handlers=EventDispatcher(),
    )

    result = await harness.run(RunRequest("echo hello", tmp_path))

    assert result.usage.cumulative == TokenUsage(
        input_tokens=260, output_tokens=50, cached_tokens=64
    )
    assert result.usage.context_tokens == 160


@pytest.mark.asyncio
async def test_stops_model_stream_after_stream_done(tmp_path: Path) -> None:
    model = EarlyStreamDoneModel()
    harness = make_harness(
        model=model,
        tools=ToolRegistry(),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        handlers=EventDispatcher(),
    )

    result = await harness.run(RunRequest("hi", tmp_path))

    assert result.final_message == "done"
    assert model.extra_chunks_yielded == 0


@pytest.mark.asyncio
async def test_persists_reasoning_in_checkpoint(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    harness = make_harness(
        model=ReasoningModel(),
        tools=ToolRegistry(),
        checkpoints=store,
        handlers=EventDispatcher(),
    )

    result = await harness.run(RunRequest("solve it", tmp_path))

    assert result.final_message == "the answer"
    loaded = store.load_state(result.run_id)
    assert loaded.reasoning_log == ("weighing options",)
    assert loaded.messages[-1].content == "the answer"


@pytest.mark.asyncio
async def test_injects_milky_frog_identity_before_user_prompt(tmp_path: Path) -> None:
    harness = make_harness(
        model=IdentityCapturingModel(),
        tools=ToolRegistry(),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        handlers=EventDispatcher(),
    )

    result = await harness.run(RunRequest("Who are you?", tmp_path))

    assert result.final_message == "I am Milky Frog."
