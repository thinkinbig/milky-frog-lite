from __future__ import annotations

from pathlib import Path

import pytest

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.domain import RunResult, RunState, RunStatus
from milky_frog.handlers import (
    CheckpointHandler,
    EventDispatcher,
    HandlerContext,
    RunCancelled,
    RunFailed,
    RunNotice,
    RunTurnEnd,
    RunTurnStart,
)
from milky_frog.harness.emitter import RunEmitter


def _make_emitter(store: SqliteCheckpointStore, registry: EventDispatcher) -> RunEmitter:
    CheckpointHandler(store).register(registry)
    return RunEmitter(registry)


@pytest.mark.asyncio
async def test_run_cancelled_persists_checkpoint_before_handler(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    registry = EventDispatcher()
    checkpoint_seen = False

    @registry.on(RunCancelled)
    async def record(_event: RunCancelled, _ctx: HandlerContext) -> None:
        nonlocal checkpoint_seen
        run = store.get_run(_event.run_id)
        checkpoint_seen = run is not None and run.status is RunStatus.CANCELLED

    emitter = _make_emitter(store, registry)
    state = RunState(run_id="run-1", workspace=tmp_path)
    store.create_run(state.run_id, tmp_path)

    await emitter.run_cancelled(
        state,
        RunResult(state.run_id, RunStatus.CANCELLED, "cancelled", 0),
    )

    assert checkpoint_seen is True


@pytest.mark.asyncio
async def test_run_failed_persists_checkpoint_before_handler(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    registry = EventDispatcher()
    checkpoint_seen = False

    @registry.on(RunFailed)
    async def record(_event: RunFailed, _ctx: HandlerContext) -> None:
        nonlocal checkpoint_seen
        run = store.get_run(_event.run_id)
        checkpoint_seen = run is not None and run.status is RunStatus.FAILED

    emitter = _make_emitter(store, registry)
    state = RunState(run_id="run-2", workspace=tmp_path)
    store.create_run(state.run_id, tmp_path)

    await emitter.run_failed(
        state,
        RunResult(state.run_id, RunStatus.FAILED, "RuntimeError: boom", 0),
    )

    assert checkpoint_seen is True


@pytest.mark.asyncio
async def test_finish_failed_returns_result_and_notifies(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    registry = EventDispatcher()
    failed: list[RunFailed] = []

    @registry.on(RunFailed)
    async def record(event: RunFailed, _ctx: HandlerContext) -> None:
        failed.append(event)

    emitter = _make_emitter(store, registry)
    state = RunState(run_id="run-3", workspace=tmp_path)
    store.create_run(state.run_id, tmp_path)

    result = await emitter.finish_failed(state, RuntimeError("boom"))

    assert result.status is RunStatus.FAILED
    assert result.final_message == "RuntimeError: boom"
    assert len(failed) == 1
    assert failed[0].result is result


@pytest.mark.asyncio
async def test_turn_started_notifies_handler(tmp_path: Path) -> None:
    registry = EventDispatcher()
    seen: list[RunTurnStart] = []

    @registry.on(RunTurnStart)
    async def record(event: RunTurnStart, _ctx: HandlerContext) -> None:
        del _ctx
        seen.append(event)

    emitter = RunEmitter(registry)
    await emitter.turn_started("run-1", model_call=3)

    assert len(seen) == 1
    assert seen[0].run_id == "run-1"
    assert seen[0].model_call == 3


@pytest.mark.asyncio
async def test_turn_ended_notifies_handler(tmp_path: Path) -> None:
    registry = EventDispatcher()
    seen: list[RunTurnEnd] = []

    @registry.on(RunTurnEnd)
    async def record(event: RunTurnEnd, _ctx: HandlerContext) -> None:
        del _ctx
        seen.append(event)

    emitter = RunEmitter(registry)
    await emitter.turn_ended("run-1", model_call=2)

    assert len(seen) == 1
    assert seen[0].run_id == "run-1"
    assert seen[0].model_call == 2


@pytest.mark.asyncio
async def test_run_notice_notifies_handler() -> None:
    registry = EventDispatcher()
    seen: list[RunNotice] = []

    @registry.on(RunNotice)
    async def record(event: RunNotice, _ctx: HandlerContext) -> None:
        del _ctx
        seen.append(event)

    emitter = RunEmitter(registry)
    await emitter.run_notice("run-1", "retrying model connection", level="warning")

    assert len(seen) == 1
    assert seen[0].message == "retrying model connection"
    assert seen[0].level == "warning"
