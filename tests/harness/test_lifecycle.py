"""Harness lifecycle: run events, pause, cancel, fail, handler isolation."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic import BaseModel

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.domain import (
    ModelChunk,
    ModelRequest,
    ModelResponse,
    RunCancellation,
    RunRequest,
    RunResult,
    RunStatus,
    StreamDone,
    ToolCall,
)
from milky_frog.handlers import (
    HandlerContext,
    LifecycleBus,
    RunBeforeTool,
    RunCancelled,
    RunFailed,
    RunPaused,
    RunStarted,
    RunTurnEnd,
    RunTurnStart,
)
from milky_frog.harness.runner import Harness
from milky_frog.harness.tools import ToolContext, ToolRegistry, ToolResult
from tests.checkpoint_helpers import run_status, tool_messages
from tests.stubs import EchoTool, FakeModel, SlowStreamModel


@pytest.mark.asyncio
async def test_dispatches_run_lifecycle_events(tmp_path: Path) -> None:
    registry = LifecycleBus()
    seen: list[str] = []

    @registry.subscribe
    async def record(event: object, ctx: HandlerContext) -> None:
        del ctx
        seen.append(type(event).__name__)

    harness = Harness(
        model=FakeModel(),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        handlers=registry,
    )

    result = await harness.run(RunRequest("echo hello", tmp_path))

    assert result.status is RunStatus.COMPLETED
    assert seen[0] == "RunBeforeStart"
    assert seen[1] == "RunStarted"
    assert seen[-1] == "RunCompleted"
    assert "RunPaused" not in seen
    assert "RunCancelled" not in seen


@pytest.mark.asyncio
async def test_dispatches_run_paused_event(tmp_path: Path) -> None:
    registry = LifecycleBus()
    paused: list[RunPaused] = []

    @registry.on(RunPaused)
    async def record(event: RunPaused, ctx: HandlerContext) -> None:
        del ctx
        paused.append(event)

    class NoToolModel:
        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            del request
            yield StreamDone(
                ModelResponse(tool_calls=(ToolCall("call-1", "echo", {"text": "hello"}),))
            )

    harness = Harness(
        model=NoToolModel(),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        handlers=registry,
    )

    result = await harness.run(RunRequest("loop forever", tmp_path, max_model_calls=1))

    assert result.status is RunStatus.PAUSED_LIMIT
    assert len(paused) == 1
    assert paused[0].status is RunStatus.PAUSED_LIMIT


@pytest.mark.asyncio
async def test_cancellation_stops_run(tmp_path: Path) -> None:
    registry = LifecycleBus()
    cancelled: list[RunCancelled] = []

    @registry.on(RunCancelled)
    async def record(event: RunCancelled, ctx: HandlerContext) -> None:
        del ctx
        cancelled.append(event)

    cancellation = RunCancellation()
    harness = Harness(
        model=SlowStreamModel(),
        tools=ToolRegistry(),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        handlers=registry,
    )

    async def run_and_cancel() -> RunResult:
        task = asyncio.create_task(
            harness.run(RunRequest("slow", tmp_path, cancellation=cancellation))
        )
        await asyncio.sleep(0.01)
        cancellation.cancel()
        return await task

    result = await run_and_cancel()

    assert result.status is RunStatus.CANCELLED
    assert len(cancelled) == 1
    assert cancelled[0].reason == "cancelled"
    assert cancelled[0].model_calls == 0
    assert result.model_calls == 0
    store = SqliteCheckpointStore(tmp_path / "state.db")
    assert run_status(store, result.run_id) is RunStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancellation_during_tool_execution(tmp_path: Path) -> None:
    class SlowToolInput(BaseModel):
        pass

    class SlowTool:
        name = "slow"
        description = "Slow tool"
        input_model = SlowToolInput

        async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
            del input
            await asyncio.sleep(0.1)
            return ToolResult("done")

    class SlowToolModel:
        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            del request
            yield StreamDone(ModelResponse(tool_calls=(ToolCall("call-1", "slow", {}),)))

    cancellation = RunCancellation()
    harness = Harness(
        model=SlowToolModel(),
        tools=ToolRegistry((SlowTool(),)),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        handlers=LifecycleBus(),
    )

    async def run_and_cancel() -> RunResult:
        task = asyncio.create_task(
            harness.run(RunRequest("slow tool", tmp_path, cancellation=cancellation))
        )
        await asyncio.sleep(0.01)
        cancellation.cancel()
        return await task

    result = await run_and_cancel()

    assert result.status is RunStatus.CANCELLED
    store = SqliteCheckpointStore(tmp_path / "state.db")
    assert not tool_messages(store.load_state(result.run_id))


def test_tool_context_exposes_cancellation_poll() -> None:
    cancellation = RunCancellation()
    context = ToolContext("run-1", Path("."), cancellation)

    assert context.is_cancelled() is False
    cancellation.cancel()
    assert context.is_cancelled() is True


@pytest.mark.asyncio
async def test_cancelled_handler_runs_after_checkpoint(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    registry = LifecycleBus()
    checkpoint_seen = False

    @registry.on(RunCancelled)
    async def record(event: RunCancelled, ctx: HandlerContext) -> None:
        del ctx
        nonlocal checkpoint_seen
        run = store.get_run(event.run_id)
        checkpoint_seen = run is not None and run.status is RunStatus.CANCELLED

    cancellation = RunCancellation()
    harness = Harness(
        model=SlowStreamModel(),
        tools=ToolRegistry(),
        checkpoints=store,
        handlers=registry,
    )

    async def run_and_cancel() -> RunResult:
        task = asyncio.create_task(
            harness.run(RunRequest("slow", tmp_path, cancellation=cancellation))
        )
        await asyncio.sleep(0.01)
        cancellation.cancel()
        return await task

    await run_and_cancel()

    assert checkpoint_seen is True


@pytest.mark.asyncio
async def test_external_cancellation_reraises(tmp_path: Path) -> None:
    harness = Harness(
        model=SlowStreamModel(),
        tools=ToolRegistry(),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        handlers=LifecycleBus(),
    )

    task = asyncio.create_task(harness.run(RunRequest("slow", tmp_path)))
    await asyncio.sleep(0.01)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_external_cancellation_kills_orphan_stream(tmp_path: Path) -> None:
    """An external abort must cancel the model stream, not leave it running.

    Regression: ``_run_cancellable`` used to drop its child task on external
    ``CancelledError``, so the model kept streaming after the Run was reported
    cancelled (the TUI rendered a full answer under an 'interrupted' notice)."""

    class TrackingStreamModel:
        def __init__(self) -> None:
            self.completed = False

        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            del request
            await asyncio.sleep(0.05)
            self.completed = True
            yield StreamDone(ModelResponse(content="done"))

    model = TrackingStreamModel()
    harness = Harness(
        model=model,
        tools=ToolRegistry(),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        handlers=LifecycleBus(),
    )

    task = asyncio.create_task(harness.run(RunRequest("slow", tmp_path)))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Give any orphaned stream task time to finish; it must have been cancelled.
    await asyncio.sleep(0.1)
    assert model.completed is False


@pytest.mark.asyncio
async def test_dispatches_run_failed_event(tmp_path: Path) -> None:
    registry = LifecycleBus()
    failed: list[RunFailed] = []

    @registry.on(RunFailed)
    async def record(event: RunFailed, ctx: HandlerContext) -> None:
        del ctx
        failed.append(event)

    class BrokenModel:
        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            del request
            raise RuntimeError("boom")
            yield StreamDone(ModelResponse())  # pragma: no cover

    harness = Harness(
        model=BrokenModel(),
        tools=ToolRegistry(),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        handlers=registry,
    )

    with pytest.raises(RuntimeError, match="boom"):
        await harness.run(RunRequest("fail", tmp_path))

    assert len(failed) == 1
    assert isinstance(failed[0].error, RuntimeError)
    store = SqliteCheckpointStore(tmp_path / "state.db")
    assert run_status(store, failed[0].run_id) is RunStatus.FAILED


@pytest.mark.asyncio
async def test_before_tool_handler_cannot_mutate_executed_call(tmp_path: Path) -> None:
    handlers = LifecycleBus()

    @handlers.observe(RunBeforeTool)
    async def mutate_handler_copy(event: RunBeforeTool, ctx: HandlerContext) -> None:
        del ctx
        event.call.arguments["text"] = "tampered"

    store = SqliteCheckpointStore(tmp_path / "state.db")
    result = await Harness(FakeModel(), ToolRegistry((EchoTool(),)), store, handlers).run(
        RunRequest("echo hello", tmp_path)
    )

    loaded = store.load_state(result.run_id)
    assert tool_messages(loaded)[0] == "hello"


@pytest.mark.asyncio
async def test_run_started_handler_cannot_control_live_run(tmp_path: Path) -> None:
    handlers = LifecycleBus()

    @handlers.observe(RunStarted)
    async def mutate_handler_snapshot(event: RunStarted, ctx: HandlerContext) -> None:
        del ctx
        assert event.request.cancellation is not None
        event.request.cancellation.cancel()

    class SimpleModel:
        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            yield StreamDone(ModelResponse(content="done"))

    cancellation = RunCancellation()
    result = await Harness(
        SimpleModel(),
        ToolRegistry(),
        SqliteCheckpointStore(tmp_path / "state.db"),
        handlers,
    ).run(RunRequest("go", tmp_path, cancellation=cancellation))

    assert result.status is RunStatus.COMPLETED
    assert not cancellation.is_cancelled


@pytest.mark.asyncio
async def test_dispatches_run_turn_events(tmp_path: Path) -> None:
    """RunTurnStart and RunTurnEnd fire with correct numbering across two turns."""
    registry = LifecycleBus()
    starts: list[RunTurnStart] = []
    ends: list[RunTurnEnd] = []

    @registry.on(RunTurnStart)
    async def on_start(event: RunTurnStart, ctx: HandlerContext) -> None:
        del ctx
        starts.append(event)

    @registry.on(RunTurnEnd)
    async def on_end(event: RunTurnEnd, ctx: HandlerContext) -> None:
        del ctx
        ends.append(event)

    harness = Harness(
        model=FakeModel(),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        handlers=registry,
    )

    await harness.run(RunRequest("echo hello", tmp_path, max_model_calls=2))

    # FakeModel with EchoTool: each turn has one model call + one tool call.
    # With max_model_calls=2, two full turns happen before pause.
    assert len(starts) >= 2
    assert len(ends) >= 2
    assert starts[0].model_call == 1
    assert ends[0].model_call == 1
    assert starts[1].model_call == 2
    assert ends[1].model_call == 2


@pytest.mark.asyncio
async def test_turn_events_fire_before_complete(tmp_path: Path) -> None:
    """RunTurnEnd fires before RunCompleted when model returns no tool calls."""
    registry = LifecycleBus()
    order: list[str] = []

    @registry.subscribe
    async def record(event: object, ctx: HandlerContext) -> None:
        del ctx
        order.append(type(event).__name__)

    class ContentModel:
        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            del request
            yield StreamDone(ModelResponse(content="done"))

    harness = Harness(
        model=ContentModel(),
        tools=ToolRegistry(),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        handlers=registry,
    )

    await harness.run(RunRequest("go", tmp_path))

    assert order == [
        "RunBeforeStart",
        "RunStarted",
        "RunTurnStart",
        "RunBeforeModel",
        "RunAfterModel",
        "RunTurnEnd",
        "RunCompleted",
    ]
