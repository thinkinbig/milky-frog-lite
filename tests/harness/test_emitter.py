from __future__ import annotations

from pathlib import Path

import pytest

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.domain import RunResult, RunState, RunStatus
from milky_frog.handlers import HandlerRegistry, RunCancelled, RunFailed
from milky_frog.harness.emitter import RunEmitter


@pytest.mark.asyncio
async def test_run_cancelled_persists_checkpoint_before_handler(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    registry = HandlerRegistry()
    checkpoint_seen = False

    @registry.on(RunCancelled)
    async def record(_event: RunCancelled) -> None:
        nonlocal checkpoint_seen
        run = store.get_run(_event.run_id)
        checkpoint_seen = run is not None and run.status is RunStatus.CANCELLED

    emitter = RunEmitter(store, registry)
    state = RunState(run_id="run-1", workspace=tmp_path)
    store.create_run(state.run_id, tmp_path)
    result = RunResult(state.run_id, RunStatus.CANCELLED, "cancelled", 0)

    await emitter.run_cancelled(state, "cancelled", result)

    assert checkpoint_seen is True


@pytest.mark.asyncio
async def test_run_failed_persists_checkpoint_before_handler(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    registry = HandlerRegistry()
    checkpoint_seen = False

    @registry.on(RunFailed)
    async def record(_event: RunFailed) -> None:
        nonlocal checkpoint_seen
        run = store.get_run(_event.run_id)
        checkpoint_seen = run is not None and run.status is RunStatus.FAILED

    emitter = RunEmitter(store, registry)
    state = RunState(run_id="run-2", workspace=tmp_path)
    store.create_run(state.run_id, tmp_path)

    await emitter.run_failed(state, RuntimeError("boom"))

    assert checkpoint_seen is True
