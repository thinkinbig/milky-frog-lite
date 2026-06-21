"""Resume: unit tests for ResumeGate + Harness integration tests."""

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
    RunRequest,
    RunState,
    RunStatus,
    StreamDone,
    TextDelta,
)
from milky_frog.handlers import HandlerRegistry
from milky_frog.harness import Harness, ResumeError
from milky_frog.harness.resume import (
    AdvancePlan,
    CompleteShortcutPlan,
    ResumeGate,
)
from milky_frog.harness.sandbox import LocalSandbox
from milky_frog.harness.state import (
    INTERRUPTED_TOOL_RESULT,
    append_model_response,
    append_user_message,
)
from milky_frog.harness.steering import SteeringPolicy
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
)

# ── ResumeGate unit tests ─────────────────────────────────────────────


def _steering_append(state: RunState, content: str) -> RunState:
    return append_user_message(state, content)


def test_validate_rejects_unknown_run() -> None:
    with pytest.raises(ResumeError, match="unknown Run"):
        ResumeGate.validate(None, "missing", None)


def test_validate_rejects_completed_without_prompt(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    seed_run(store, "done", tmp_path, status=RunStatus.COMPLETED, final_message="ok")
    stored = store.get_run("done")
    assert stored is not None

    with pytest.raises(ResumeError, match="no pending work"):
        ResumeGate.validate(stored, "done", None)


def test_prepare_returns_complete_shortcut_plan(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    state = seed_run(store, "run-1", tmp_path)
    state = append_model_response(state, ModelResponse(content="all done"))
    store.save_state("run-1", state, status=RunStatus.COMPLETED, final_message="all done")
    stored = store.get_run("run-1")
    assert stored is not None

    gate = ResumeGate(store, SteeringPolicy(_steering_append))
    plan = gate.prepare(
        "run-1",
        stored,
        sandbox=LocalSandbox(tmp_path),
        prompt=None,
        steering=None,
        updated_at=stored.updated_at,
    )

    assert isinstance(plan, CompleteShortcutPlan)
    assert plan.tail == "all done"


def test_prepare_returns_advance_plan_with_prompt(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    state = seed_run(store, "run-2", tmp_path)
    state = append_model_response(state, ModelResponse(content="done"))
    store.save_state("run-2", state, status=RunStatus.COMPLETED, final_message="done")
    stored = store.get_run("run-2")
    assert stored is not None

    gate = ResumeGate(store, SteeringPolicy(_steering_append))
    plan = gate.prepare(
        "run-2",
        stored,
        sandbox=LocalSandbox(tmp_path),
        prompt="follow up",
        steering=None,
        updated_at=stored.updated_at,
    )

    assert isinstance(plan, AdvancePlan)
    assert plan.state.messages[-1].role is MessageRole.USER
    assert plan.state.messages[-1].content == "follow up"


# ── Harness integration: resume ───────────────────────────────────────


@pytest.mark.asyncio
async def test_fold_reconstructs_live_transcript(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    harness = Harness(FakeModel(), ToolRegistry((EchoTool(),)), store, HandlerRegistry())

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
    harness = Harness(PauseThenFinishModel(), ToolRegistry((EchoTool(),)), store, HandlerRegistry())

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

    harness = Harness(
        InterruptionAwareModel(), ToolRegistry((EchoTool(),)), store, HandlerRegistry()
    )
    result = await harness.resume(run_id, max_model_calls=30)

    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "recovered"
    loaded = store.load_state(run_id)
    assert INTERRUPTED_TOOL_RESULT in tool_messages(loaded)


@pytest.mark.asyncio
async def test_resume_rejects_completed_run(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    harness = Harness(FakeModel(), ToolRegistry((EchoTool(),)), store, HandlerRegistry())

    done = await harness.run(RunRequest("echo hello", tmp_path))
    assert done.status is RunStatus.COMPLETED

    with pytest.raises(ResumeError, match="no pending work"):
        await harness.resume(done.run_id, max_model_calls=30)


@pytest.mark.asyncio
async def test_resume_rejects_failed_without_prompt(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    run_id = "run-failed-no-prompt"
    seed_failed_run(store, run_id, tmp_path)

    harness = Harness(FakeModel(), ToolRegistry(), store, HandlerRegistry())
    with pytest.raises(ResumeError, match="no pending work"):
        await harness.resume(run_id, max_model_calls=30)


@pytest.mark.asyncio
async def test_resume_with_prompt_continues_failed_run(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    run_id = "run-failed"
    seed_failed_run(store, run_id, tmp_path)

    harness = Harness(ContinuationModel("try again"), ToolRegistry(), store, HandlerRegistry())
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

    harness = Harness(RecoveryModel(), ToolRegistry((EchoTool(),)), store, HandlerRegistry())
    result = await harness.resume(run_id, max_model_calls=30)

    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "recovered"


@pytest.mark.asyncio
async def test_resume_rejects_live_owned_run(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    run_id = "run-live"
    seed_run(store, run_id, tmp_path)
    harness = Harness(FakeModel(), ToolRegistry((EchoTool(),)), store, HandlerRegistry())

    with store.claim(run_id), pytest.raises(ResumeError, match="already active"):
        await harness.resume(run_id, max_model_calls=30)


@pytest.mark.asyncio
async def test_resume_finalizes_clean_response_without_model_call(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    run_id = "run-final-response"
    seed_assistant_turn(store, run_id, tmp_path, content="already done")

    class NeverCalledModel:
        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            del request
            if False:  # pragma: no cover
                yield TextDelta("")
            raise AssertionError("model must not be called")

    result = await Harness(NeverCalledModel(), ToolRegistry(), store, HandlerRegistry()).resume(
        run_id, max_model_calls=30
    )

    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "already done"
    assert run_status(store, run_id) is RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_resume_projects_running_before_advance(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    harness = Harness(FakeModel(), ToolRegistry((EchoTool(),)), store, HandlerRegistry())
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
    second = Harness(model, ToolRegistry((EchoTool(),)), store, HandlerRegistry())
    await second.resume(done.run_id, max_model_calls=30, prompt="follow up")

    assert model.seen_running


@pytest.mark.asyncio
async def test_resume_rejects_unknown_run(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    harness = Harness(FakeModel(), ToolRegistry(), store, HandlerRegistry())

    with pytest.raises(ResumeError, match="unknown Run"):
        await harness.resume("does-not-exist", max_model_calls=30)


@pytest.mark.asyncio
async def test_resume_with_prompt_continues_completed_run(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    first = Harness(FakeModel(), ToolRegistry((EchoTool(),)), store, HandlerRegistry())
    done = await first.run(RunRequest("echo hello", tmp_path))
    assert done.status is RunStatus.COMPLETED

    second = Harness(
        ContinuationModel("follow up"), ToolRegistry((EchoTool(),)), store, HandlerRegistry()
    )
    result = await second.resume(done.run_id, max_model_calls=30, prompt="follow up")

    assert result.run_id == done.run_id
    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "ack"
    loaded = store.load_state(done.run_id)
    assert user_messages(loaded) == ("echo hello", "follow up")
