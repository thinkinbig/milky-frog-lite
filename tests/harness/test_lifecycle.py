"""Harness lifecycle: run events, pause, cancel, fail, handler isolation."""

from __future__ import annotations

import asyncio
from collections import defaultdict
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
from milky_frog.events import (
    EventHub,
    RunBeforeTool,
    RunCancelled,
    RunFailed,
    RunPaused,
    RunStarted,
    RunTurnEnd,
    RunTurnStart,
)
from milky_frog.harness.state import unmatched_tool_calls
from milky_frog.harness.tools import ToolContext, ToolRegistry, ToolResult
from tests.checkpoint_helpers import run_status, tool_messages
from tests.stubs import EchoTool, FakeModel, SlowStreamModel, make_harness


class DelayedToolInput(BaseModel):
    label: str
    delay: float = 0.0


class DelayedTool:
    """Sleeps ``delay`` seconds then echoes ``label`` — used to prove concurrency."""

    name = "delayed"
    description = "Sleeps then echoes label"
    input_model: type[BaseModel] = DelayedToolInput

    def __init__(self) -> None:
        self.completed: defaultdict[str, asyncio.Event] = defaultdict(asyncio.Event)
        """Per-label completion signal, so a test can act on a finished call
        instead of sleeping long enough to assume it finished."""

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        del context
        parsed = DelayedToolInput.model_validate(input)
        await asyncio.sleep(parsed.delay)
        self.completed[parsed.label].set()
        return ToolResult(parsed.label)


class MultiCallModel:
    """First turn returns a fixed batch of tool_calls; second turn completes."""

    def __init__(self, calls: tuple[ToolCall, ...]) -> None:
        self._calls = calls
        self.turn = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        self.turn += 1
        if self.turn == 1:
            yield StreamDone(ModelResponse(tool_calls=self._calls))
            return
        yield StreamDone(ModelResponse(content="done"))


@pytest.mark.asyncio
async def test_dispatches_run_lifecycle_events(tmp_path: Path) -> None:
    registry = EventHub()
    seen: list[str] = []

    @registry.subscribe
    async def record(event: object, _ctx=None) -> None:
        seen.append(type(event).__name__)

    harness = make_harness(
        model=FakeModel(),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        hub=registry,
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
    registry = EventHub()
    paused: list[RunPaused] = []

    @registry.on(RunPaused)
    async def record(event: RunPaused, _ctx=None) -> None:
        paused.append(event)

    class NoToolModel:
        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            del request
            yield StreamDone(
                ModelResponse(tool_calls=(ToolCall("call-1", "echo", {"text": "hello"}),))
            )

    harness = make_harness(
        model=NoToolModel(),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        hub=registry,
    )

    result = await harness.run(RunRequest("loop forever", tmp_path, max_model_calls=1))

    assert result.status is RunStatus.PAUSED_LIMIT
    assert len(paused) == 1
    assert paused[0].result.status is RunStatus.PAUSED_LIMIT


@pytest.mark.asyncio
async def test_cancellation_stops_run(tmp_path: Path) -> None:
    registry = EventHub()
    cancelled: list[RunCancelled] = []

    @registry.on(RunCancelled)
    async def record(event: RunCancelled, _ctx=None) -> None:
        cancelled.append(event)

    cancellation = RunCancellation()
    harness = make_harness(
        model=SlowStreamModel(),
        tools=ToolRegistry(),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        hub=registry,
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
    assert cancelled[0].result.final_message == "cancelled"
    assert cancelled[0].result.model_calls == 0
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
    harness = make_harness(
        model=SlowToolModel(),
        tools=ToolRegistry((SlowTool(),)),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        hub=EventHub(),
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
    registry = EventHub()
    checkpoint_seen = False

    @registry.on(RunCancelled)
    async def record(event: RunCancelled, _ctx=None) -> None:
        nonlocal checkpoint_seen
        run = store.get_run(event.run_id)
        checkpoint_seen = run is not None and run.status is RunStatus.CANCELLED

    cancellation = RunCancellation()
    harness = make_harness(
        model=SlowStreamModel(),
        tools=ToolRegistry(),
        checkpoints=store,
        hub=registry,
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
    harness = make_harness(
        model=SlowStreamModel(),
        tools=ToolRegistry(),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        hub=EventHub(),
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
    harness = make_harness(
        model=model,
        tools=ToolRegistry(),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        hub=EventHub(),
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
    registry = EventHub()
    failed: list[RunFailed] = []

    @registry.on(RunFailed)
    async def record(event: RunFailed, _ctx=None) -> None:
        failed.append(event)

    class BrokenModel:
        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            del request
            raise RuntimeError("boom")
            yield StreamDone(ModelResponse())  # pragma: no cover

    harness = make_harness(
        model=BrokenModel(),
        tools=ToolRegistry(),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        hub=registry,
    )

    result = await harness.run(RunRequest("fail", tmp_path))

    assert result.status is RunStatus.FAILED
    assert "boom" in result.final_message
    assert len(failed) == 1
    assert failed[0].result.status is RunStatus.FAILED
    assert "boom" in failed[0].result.final_message
    store = SqliteCheckpointStore(tmp_path / "state.db")
    assert run_status(store, failed[0].run_id) is RunStatus.FAILED


@pytest.mark.asyncio
async def test_before_tool_handler_cannot_mutate_executed_call(tmp_path: Path) -> None:
    handlers = EventHub()

    @handlers.observe(RunBeforeTool)
    async def mutate_handler_copy(event: RunBeforeTool, _ctx=None) -> None:
        event.call.arguments["text"] = "tampered"

    store = SqliteCheckpointStore(tmp_path / "state.db")
    result = await make_harness(FakeModel(), ToolRegistry((EchoTool(),)), store, handlers).run(
        RunRequest("echo hello", tmp_path)
    )

    loaded = store.load_state(result.run_id)
    assert tool_messages(loaded)[0] == "hello"


@pytest.mark.asyncio
async def test_run_started_handler_cannot_control_live_run(tmp_path: Path) -> None:
    handlers = EventHub()

    @handlers.observe(RunStarted)
    async def mutate_handler_snapshot(event: RunStarted, _ctx=None) -> None:
        assert event.request.cancellation is not None
        event.request.cancellation.cancel()

    class SimpleModel:
        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            yield StreamDone(ModelResponse(content="done"))

    cancellation = RunCancellation()
    result = await make_harness(
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
    registry = EventHub()
    starts: list[RunTurnStart] = []
    ends: list[RunTurnEnd] = []

    @registry.on(RunTurnStart)
    async def on_start(event: RunTurnStart, _ctx=None) -> None:
        starts.append(event)

    @registry.on(RunTurnEnd)
    async def on_end(event: RunTurnEnd, _ctx=None) -> None:
        ends.append(event)

    harness = make_harness(
        model=FakeModel(),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        hub=registry,
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
    registry = EventHub()
    order: list[str] = []

    @registry.subscribe
    async def record(event: object, _ctx=None) -> None:
        order.append(type(event).__name__)

    class ContentModel:
        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            del request
            yield StreamDone(ModelResponse(content="done"))

    harness = make_harness(
        model=ContentModel(),
        tools=ToolRegistry(),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        hub=registry,
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


@pytest.mark.asyncio
async def test_concurrent_tool_calls_run_in_parallel(tmp_path: Path) -> None:
    """A batch of calls that need no approval runs concurrently, not in relay."""
    calls = tuple(
        ToolCall(f"call-{i}", "delayed", {"label": f"t{i}", "delay": 0.2}) for i in range(3)
    )
    harness = make_harness(
        model=MultiCallModel(calls),
        tools=ToolRegistry((DelayedTool(),)),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        hub=EventHub(),
    )

    start = asyncio.get_event_loop().time()
    result = await harness.run(RunRequest("go", tmp_path, max_model_calls=2))
    elapsed = asyncio.get_event_loop().time() - start

    assert result.status is RunStatus.COMPLETED
    # Sequential execution would take >= 0.6s; concurrent stays near 0.2s.
    assert elapsed < 0.35


@pytest.mark.asyncio
async def test_concurrent_tool_calls_preserve_request_order(tmp_path: Path) -> None:
    """Tool results land in the transcript in request order, not completion order."""
    calls = (
        ToolCall("call-1", "delayed", {"label": "slow", "delay": 0.15}),
        ToolCall("call-2", "delayed", {"label": "fast", "delay": 0.01}),
        ToolCall("call-3", "delayed", {"label": "medium", "delay": 0.08}),
    )
    store = SqliteCheckpointStore(tmp_path / "state.db")
    harness = make_harness(
        model=MultiCallModel(calls),
        tools=ToolRegistry((DelayedTool(),)),
        checkpoints=store,
        hub=EventHub(),
    )

    result = await harness.run(RunRequest("go", tmp_path, max_model_calls=2))

    assert result.status is RunStatus.COMPLETED
    state = store.load_state(result.run_id)
    assert tool_messages(state) == ("slow", "fast", "medium")


@pytest.mark.asyncio
async def test_batch_runs_allowed_subset_before_halting_on_approval(tmp_path: Path) -> None:
    """A batch with one call needing approval runs the rest first, then halts."""

    class DangerousInput(BaseModel):
        pass

    class DangerousTool:
        name = "dangerous"
        description = "Needs approval"
        input_model: type[BaseModel] = DangerousInput

        async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
            del context, input
            return ToolResult("should not run without approval")

    class MixedApprovalModel:
        def __init__(self) -> None:
            self.turn = 0

        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            del request
            self.turn += 1
            if self.turn == 1:
                yield StreamDone(
                    ModelResponse(
                        tool_calls=(
                            ToolCall("call-1", "echo", {"text": "a"}),
                            ToolCall("call-2", "dangerous", {}),
                        )
                    )
                )
                return
            yield StreamDone(ModelResponse(content="done"))

    store = SqliteCheckpointStore(tmp_path / "state.db")
    harness = make_harness(
        model=MixedApprovalModel(),
        tools=ToolRegistry((EchoTool(), DangerousTool())),
        checkpoints=store,
        hub=EventHub(),
    )
    harness.policy.require_approval("dangerous")

    result = await harness.run(RunRequest("go", tmp_path))

    assert result.status is RunStatus.WAITING_FOR_APPROVAL
    state = store.load_state(result.run_id)
    # The call that never needed approval already resolved (concurrently, as
    # part of the runnable subset); the dangerous call itself never executed.
    assert tool_messages(state) == ("a",)


@pytest.mark.asyncio
async def test_batch_halts_exposing_every_call_needing_approval(tmp_path: Path) -> None:
    """Multiple NEEDS_APPROVAL calls in one batch all surface as pending, not just one."""

    class DangerousInput(BaseModel):
        pass

    class DangerousTool:
        name = "dangerous"
        description = "Needs approval"
        input_model: type[BaseModel] = DangerousInput

        async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
            del context, input
            return ToolResult("should not run without approval")

    calls = (
        ToolCall("call-1", "delayed", {"label": "t0", "delay": 0.0}),
        ToolCall("call-2", "dangerous", {}),
        ToolCall("call-3", "dangerous", {}),
    )
    store = SqliteCheckpointStore(tmp_path / "state.db")
    harness = make_harness(
        model=MultiCallModel(calls),
        tools=ToolRegistry((DelayedTool(), DangerousTool())),
        checkpoints=store,
        hub=EventHub(),
    )
    harness.policy.require_approval("dangerous")

    result = await harness.run(RunRequest("go", tmp_path))

    assert result.status is RunStatus.WAITING_FOR_APPROVAL
    state = store.load_state(result.run_id)
    assert tool_messages(state) == ("t0",)
    pending = unmatched_tool_calls(state.messages)
    assert {call.id for call in pending} == {"call-2", "call-3"}


@pytest.mark.asyncio
async def test_concurrent_batch_cancellation_reports_once(tmp_path: Path) -> None:
    """Cancelling mid-batch reports RunCancelled exactly once, not per concurrent task."""
    registry = EventHub()
    cancelled: list[RunCancelled] = []

    @registry.on(RunCancelled)
    async def record(event: RunCancelled, _ctx=None) -> None:
        cancelled.append(event)

    calls = tuple(
        ToolCall(f"call-{i}", "delayed", {"label": f"t{i}", "delay": 0.2}) for i in range(3)
    )
    cancellation = RunCancellation()
    harness = make_harness(
        model=MultiCallModel(calls),
        tools=ToolRegistry((DelayedTool(),)),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        hub=registry,
    )

    async def run_and_cancel() -> RunResult:
        task = asyncio.create_task(
            harness.run(RunRequest("go", tmp_path, cancellation=cancellation))
        )
        await asyncio.sleep(0.03)
        cancellation.cancel()
        return await task

    result = await run_and_cancel()

    assert result.status is RunStatus.CANCELLED
    assert len(cancelled) == 1


@pytest.mark.asyncio
async def test_concurrent_batch_cancellation_keeps_already_completed_results(
    tmp_path: Path,
) -> None:
    """A cancelled sibling must not discard a batch-mate that already finished."""
    calls = (
        ToolCall("call-1", "delayed", {"label": "fast", "delay": 0.01}),
        ToolCall("call-2", "delayed", {"label": "slow", "delay": 1.0}),
    )
    cancellation = RunCancellation()
    store = SqliteCheckpointStore(tmp_path / "state.db")
    tool = DelayedTool()
    harness = make_harness(
        model=MultiCallModel(calls),
        tools=ToolRegistry((tool,)),
        checkpoints=store,
        hub=EventHub(),
    )

    async def run_and_cancel() -> RunResult:
        task = asyncio.create_task(
            harness.run(RunRequest("go", tmp_path, cancellation=cancellation))
        )
        # Cancel once "fast" has actually finished, rather than sleeping long
        # enough to assume it has — the sleep also had to cover the model call
        # and checkpoint writes preceding the batch, so its margin varied with
        # machine load. Waiting on the completion signal cancels at the tightest
        # point instead: the same event-loop iteration "fast" returns in.
        await asyncio.wait_for(tool.completed["fast"].wait(), timeout=10)
        cancellation.cancel()
        return await task

    result = await run_and_cancel()

    assert result.status is RunStatus.CANCELLED
    state = store.load_state(result.run_id)
    # "fast" finished well before cancellation and must survive in the
    # transcript even though its batch-mate "slow" was cancelled.
    assert tool_messages(state) == ("fast",)
