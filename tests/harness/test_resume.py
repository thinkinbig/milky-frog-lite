"""Resume: Harness integration tests for checkpoint repair and continuation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.domain import (
    MessageRole,
    ModelChunk,
    ModelRequest,
    ModelResponse,
    ResumeError,
    RunRequest,
    RunStatus,
    StreamDone,
)
from milky_frog.handlers import EventDispatcher
from milky_frog.harness.state import (
    INTERRUPTED_TOOL_RESULT,
)
from milky_frog.harness.tools import ToolRegistry
from tests.checkpoint_helpers import (
    run_status,
    seed_assistant_turn,
    seed_failed_run,
    seed_interrupted_tool_run,
    seed_run,
    tool_messages,
    user_messages,
)
from tests.stubs import (
    ContinuationModel,
    EchoTool,
    FakeModel,
    PauseThenFinishModel,
    make_harness,
)

# ── Harness integration: resume ───────────────────────────────────────


@pytest.mark.asyncio
async def test_fold_reconstructs_live_transcript(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    harness = make_harness(FakeModel(), ToolRegistry((EchoTool(),)), store, EventDispatcher())

    result = await harness.run(RunRequest("echo hello", tmp_path))

    folded = store.load_state(result.run_id)
    assert [message.role for message in folded.messages] == [
        MessageRole.SYSTEM,
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    assert folded.messages[-1].content == "done"
    assert folded.completed_model_calls == result.model_calls == 2


@pytest.mark.asyncio
async def test_resume_continues_paused_run(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    harness = make_harness(
        PauseThenFinishModel(), ToolRegistry((EchoTool(),)), store, EventDispatcher()
    )

    paused = await harness.run(RunRequest("go", tmp_path, max_model_calls=1))
    assert paused.status is RunStatus.PAUSED_LIMIT

    resumed = await harness.resume(paused.run_id, max_model_calls=30)

    assert resumed.run_id == paused.run_id
    assert resumed.status is RunStatus.COMPLETED
    assert resumed.final_message == "done"
    assert resumed.model_calls == 2
    assert store.get_run(paused.run_id) is not None


@pytest.mark.asyncio
async def test_resume_repairs_interrupted_tool_call(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    run_id = "run-interrupted"
    seed_interrupted_tool_run(
        store, run_id, tmp_path, status=RunStatus.CANCELLED, final_message="cancelled"
    )

    class InterruptionAwareModel:
        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            tool_msgs = [m for m in request.messages if m.role is MessageRole.TOOL]
            assert tool_msgs and tool_msgs[-1].content == INTERRUPTED_TOOL_RESULT
            yield StreamDone(ModelResponse(content="recovered"))

    harness = make_harness(
        InterruptionAwareModel(), ToolRegistry((EchoTool(),)), store, EventDispatcher()
    )
    result = await harness.resume(run_id, max_model_calls=30)

    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "recovered"
    loaded = store.load_state(run_id)
    assert INTERRUPTED_TOOL_RESULT in tool_messages(loaded)


@pytest.mark.asyncio
async def test_resume_continues_completed_without_prompt(tmp_path: Path) -> None:
    """Resume without prompt on a COMPLETED run calls the model (no more rejection)."""
    store = SqliteCheckpointStore(tmp_path / "state.db")
    harness = make_harness(FakeModel(), ToolRegistry((EchoTool(),)), store, EventDispatcher())

    result = await harness.run(RunRequest("echo hello", tmp_path))
    assert result.status is RunStatus.COMPLETED

    # Resume without prompt — now allowed, model call happens
    continued = await harness.resume(result.run_id, max_model_calls=30)
    assert continued.run_id == result.run_id
    assert continued.status is RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_resume_continues_failed_without_prompt(tmp_path: Path) -> None:
    """Resume without prompt on a FAILED run is now allowed."""
    store = SqliteCheckpointStore(tmp_path / "state.db")
    run_id = "run-failed-no-prompt"
    seed_failed_run(store, run_id, tmp_path)

    harness = make_harness(ContinuationModel("go"), ToolRegistry(), store, EventDispatcher())
    result = await harness.resume(run_id, max_model_calls=30)
    assert result.status is RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_resume_with_prompt_continues_failed_run(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    run_id = "run-failed"
    seed_failed_run(store, run_id, tmp_path)

    harness = make_harness(ContinuationModel("try again"), ToolRegistry(), store, EventDispatcher())
    result = await harness.resume(run_id, max_model_calls=30, prompt="try again")

    assert result.status is RunStatus.COMPLETED
    assert store.get_run(run_id) is not None
    assert store.get_run(run_id).status is RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_resume_recovers_orphaned_running_run(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    run_id = "run-orphaned"
    seed_interrupted_tool_run(store, run_id, tmp_path)
    assert store.get_run(run_id) is not None
    assert store.get_run(run_id).status is RunStatus.RUNNING

    class RecoveryModel:
        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            tool_msgs = [m for m in request.messages if m.role is MessageRole.TOOL]
            assert tool_msgs and tool_msgs[-1].content == INTERRUPTED_TOOL_RESULT
            yield StreamDone(ModelResponse(content="recovered"))

    harness = make_harness(RecoveryModel(), ToolRegistry((EchoTool(),)), store, EventDispatcher())
    result = await harness.resume(run_id, max_model_calls=30)

    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "recovered"


@pytest.mark.asyncio
async def test_resume_rejects_live_owned_run(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    run_id = "run-live"
    seed_run(store, run_id, tmp_path)
    harness = make_harness(FakeModel(), ToolRegistry((EchoTool(),)), store, EventDispatcher())

    with store.claim(run_id), pytest.raises(ResumeError, match="already active"):
        await harness.resume(run_id, max_model_calls=30)


@pytest.mark.asyncio
async def test_resume_completes_via_model_when_clean_tail(tmp_path: Path) -> None:
    """Resume on a clean-tail run calls the model (shortcut removed)."""
    store = SqliteCheckpointStore(tmp_path / "state.db")
    run_id = "run-final-response"
    seed_assistant_turn(store, run_id, tmp_path, content="already done")

    harness = make_harness(ContinuationModel("go"), ToolRegistry(), store, EventDispatcher())
    result = await harness.resume(run_id, max_model_calls=30)

    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "ack"
    assert run_status(store, run_id) is RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_resume_projects_running_before_advance(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    harness = make_harness(FakeModel(), ToolRegistry((EchoTool(),)), store, EventDispatcher())
    done = await harness.run(RunRequest("echo hello", tmp_path))
    assert done.status is RunStatus.COMPLETED

    class StatusCapturingModel:
        def __init__(self) -> None:
            self.seen_running = False

        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            del request
            run = store.get_run(done.run_id)
            assert run is not None
            self.seen_running = run.status is RunStatus.RUNNING
            yield StreamDone(ModelResponse(content="ack"))

    model = StatusCapturingModel()
    second = make_harness(model, ToolRegistry((EchoTool(),)), store, EventDispatcher())
    await second.resume(done.run_id, max_model_calls=30, prompt="follow up")

    assert model.seen_running


@pytest.mark.asyncio
async def test_resume_rejects_unknown_run(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    harness = make_harness(FakeModel(), ToolRegistry(), store, EventDispatcher())

    with pytest.raises(ResumeError, match="unknown Run"):
        await harness.resume("does-not-exist", max_model_calls=30)


@pytest.mark.asyncio
async def test_resume_with_prompt_continues_completed_run(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    first = make_harness(FakeModel(), ToolRegistry((EchoTool(),)), store, EventDispatcher())
    done = await first.run(RunRequest("echo hello", tmp_path))
    assert done.status is RunStatus.COMPLETED

    second = make_harness(
        ContinuationModel("follow up"), ToolRegistry((EchoTool(),)), store, EventDispatcher()
    )
    result = await second.resume(done.run_id, max_model_calls=30, prompt="follow up")

    assert result.run_id == done.run_id
    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "ack"
    loaded = store.load_state(done.run_id)
    assert user_messages(loaded) == ("echo hello", "follow up")
