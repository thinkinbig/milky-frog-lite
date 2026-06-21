"""Steering: unit tests for SteeringPolicy + Harness integration tests."""

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
    SteeringChannel,
    StreamDone,
)
from milky_frog.handlers import HandlerRegistry
from milky_frog.harness import Harness
from milky_frog.harness.state import append_user_message
from milky_frog.harness.steering import DetachedSteeringChannel, SteeringPolicy
from milky_frog.harness.tools import ToolRegistry
from tests.checkpoint_helpers import seed_assistant_turn, user_messages
from tests.stubs import FakeSteering

# ── SteeringPolicy unit tests ─────────────────────────────────────────


class _QueueSteering:
    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)

    def drain(self) -> list[str]:
        out, self._lines = self._lines, []
        return out


def _append(state: RunState, content: str) -> RunState:
    return append_user_message(state, content)


def test_detached_steering_channel_drains_empty() -> None:
    assert DetachedSteeringChannel().drain() == []


def test_absorb_turn_boundary_persists_via_callback() -> None:
    policy = SteeringPolicy(_append)
    state = RunState(run_id="r1", workspace=Path("/tmp"))
    channel: SteeringChannel = _QueueSteering(["steer"])

    after = policy.absorb_turn_boundary(state, channel)

    assert SteeringPolicy.added_turns(state, after)
    assert after.messages[-1].content == "steer"


def test_absorb_turn_boundary_unchanged_when_empty() -> None:
    policy = SteeringPolicy(_append)
    state = RunState(run_id="r1", workspace=Path("/tmp"))

    after = policy.absorb_turn_boundary(state, _QueueSteering([]))

    assert after is state
    assert not SteeringPolicy.added_turns(state, after)


def test_drain_for_resume_folds_in_memory_only() -> None:
    policy = SteeringPolicy(_append)
    state = RunState(run_id="r1", workspace=Path("/tmp"))
    channel: SteeringChannel = _QueueSteering(["resume steer"])

    folded = policy.drain_for_resume(state, channel)

    assert folded.messages[-1].content == "resume steer"
    assert folded is not state


def test_drain_for_resume_empty_drain() -> None:
    policy = SteeringPolicy(_append)
    state = RunState(run_id="r1", workspace=Path("/tmp"))

    folded = policy.drain_for_resume(state, _QueueSteering([]))

    assert folded is state


# ── Harness integration: steering ─────────────────────────────────────


@pytest.mark.asyncio
async def test_advance_injects_steering_between_turns(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")

    class SteerAwareModel:
        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            users = [m.content for m in request.messages if m.role is MessageRole.USER]
            assert "steer me" in users
            yield StreamDone(ModelResponse(content="ok"))

    harness = Harness(SteerAwareModel(), ToolRegistry(), store, HandlerRegistry())
    result = await harness.run(RunRequest("go", tmp_path, steering=FakeSteering(["steer me"])))

    assert result.status is RunStatus.COMPLETED
    assert "steer me" in user_messages(store.load_state(result.run_id))


@pytest.mark.asyncio
async def test_advance_steering_continues_instead_of_completing(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")

    class TwoPhaseModel:
        def __init__(self) -> None:
            self.calls = 0

        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            del request
            self.calls += 1
            yield StreamDone(ModelResponse(content=f"done{self.calls}"))

    model = TwoPhaseModel()
    harness = Harness(model, ToolRegistry(), store, HandlerRegistry())
    result = await harness.run(
        RunRequest("go", tmp_path, steering=FakeSteering(["keep going"], release_on=2))
    )

    assert model.calls == 2
    assert result.final_message == "done2"
    assert result.status is RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_finish_completed_absorbs_multiple_steering_lines(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")

    class MultiSteerAwareModel:
        def __init__(self) -> None:
            self.calls = 0

        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            self.calls += 1
            if self.calls == 1:
                yield StreamDone(ModelResponse(content="done1"))
                return
            users = [m.content for m in request.messages if m.role is MessageRole.USER]
            assert users.count("first steer") == 1
            assert users.count("second steer") == 1
            yield StreamDone(ModelResponse(content="done2"))

    harness = Harness(MultiSteerAwareModel(), ToolRegistry(), store, HandlerRegistry())
    result = await harness.run(
        RunRequest(
            "go",
            tmp_path,
            steering=FakeSteering(["first steer", "second steer"], release_on=2),
        )
    )

    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "done2"
    user_events = user_messages(store.load_state(result.run_id))
    assert "first steer" in user_events
    assert "second steer" in user_events


@pytest.mark.asyncio
async def test_finish_completed_drain_catches_late_steering(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")

    class ThreePhaseModel:
        def __init__(self) -> None:
            self.calls = 0

        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            self.calls += 1
            if self.calls == 1:
                yield StreamDone(ModelResponse(content="done1"))
                return
            users = [m.content for m in request.messages if m.role is MessageRole.USER]
            assert "late steer" in users
            yield StreamDone(ModelResponse(content="done2"))

    model = ThreePhaseModel()
    harness = Harness(model, ToolRegistry(), store, HandlerRegistry())
    result = await harness.run(
        RunRequest("go", tmp_path, steering=FakeSteering(["late steer"], release_on=3))
    )

    assert model.calls == 2
    assert result.final_message == "done2"
    assert result.status is RunStatus.COMPLETED
    assert "late steer" in user_messages(store.load_state(result.run_id))


@pytest.mark.asyncio
async def test_finish_completed_drain_catches_multiple_late_lines(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")

    class LateMultiModel:
        def __init__(self) -> None:
            self.calls = 0

        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            self.calls += 1
            if self.calls == 1:
                yield StreamDone(ModelResponse(content="done1"))
                return
            users = [m.content for m in request.messages if m.role is MessageRole.USER]
            assert "first late" in users
            assert "second late" in users
            yield StreamDone(ModelResponse(content="done2"))

    harness = Harness(LateMultiModel(), ToolRegistry(), store, HandlerRegistry())
    result = await harness.run(
        RunRequest(
            "go",
            tmp_path,
            steering=FakeSteering(["first late", "second late"], release_on=3),
        )
    )

    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "done2"
    user_events = user_messages(store.load_state(result.run_id))
    assert "first late" in user_events
    assert "second late" in user_events


@pytest.mark.asyncio
async def test_resume_shortcut_with_empty_steering_drain(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    run_id = "run-resume-empty-steer"
    seed_assistant_turn(store, run_id, tmp_path, content="already done")

    class NeverCalledModel:
        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            del request
            raise AssertionError("model must not be called")

    result = await Harness(NeverCalledModel(), ToolRegistry(), store, HandlerRegistry()).resume(
        run_id, max_model_calls=30, steering=FakeSteering([], release_on=99)
    )

    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "already done"


@pytest.mark.asyncio
async def test_resume_shortcut_catches_late_steering(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    run_id = "run-resume-late-steer"
    seed_assistant_turn(store, run_id, tmp_path, content="already done")

    class LateSteerModel:
        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            users = [m.content for m in request.messages if m.role is MessageRole.USER]
            assert "late resume steer" in users
            yield StreamDone(ModelResponse(content="continued"))

    result = await Harness(LateSteerModel(), ToolRegistry(), store, HandlerRegistry()).resume(
        run_id, max_model_calls=30, steering=FakeSteering(["late resume steer"], release_on=2)
    )

    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "continued"


@pytest.mark.asyncio
async def test_resume_shortcut_folds_multiple_steer_lines(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    run_id = "run-resume-steer"
    seed_assistant_turn(store, run_id, tmp_path, content="already done")

    class SteerAwareModel:
        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            users = [m.content for m in request.messages if m.role is MessageRole.USER]
            assert "steer one" in users
            assert "steer two" in users
            yield StreamDone(ModelResponse(content="continued"))

    result = await Harness(SteerAwareModel(), ToolRegistry(), store, HandlerRegistry()).resume(
        run_id, max_model_calls=30, steering=FakeSteering(["steer one", "steer two"])
    )

    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "continued"
    user_events = user_messages(store.load_state(run_id))
    assert user_events.count("steer one") == 1
    assert user_events.count("steer two") == 1
