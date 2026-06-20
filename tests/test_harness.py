import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic import BaseModel

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.domain import (
    Message,
    MessageRole,
    ModelChunk,
    ModelRequest,
    ModelResponse,
    ReasoningDelta,
    RunCancellation,
    RunRequest,
    RunResult,
    RunStatus,
    StreamDone,
    TextDelta,
    TokenUsage,
    ToolCall,
)
from milky_frog.handlers import (
    BlockTool,
    HandlerRegistry,
    PatchToolResult,
    RunCancelled,
    RunFailed,
    RunPaused,
    TransformContext,
)
from milky_frog.handlers.events import AfterTool, BeforeModel, BeforeTool
from milky_frog.harness import Harness
from milky_frog.harness.tools import ToolContext, ToolRegistry, ToolResult


class EchoInput(BaseModel):
    text: str


class EchoTool:
    name = "echo"
    description = "Echo text"
    input_model = EchoInput

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        assert context.workspace.is_dir()
        parsed = EchoInput.model_validate(input)
        return ToolResult(parsed.text)


class FakeModel:
    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        self.calls += 1
        if self.calls == 1:
            assert request.tools[0]["function"]["name"] == "echo"
            yield StreamDone(
                ModelResponse(tool_calls=(ToolCall("call-1", "echo", {"text": "hello"}),))
            )
            return
        yield TextDelta("done")
        yield StreamDone(ModelResponse(content="done"))


class ReasoningModel:
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        yield ReasoningDelta("weighing options")
        yield TextDelta("the answer")
        yield StreamDone(ModelResponse(content="the answer", reasoning="weighing options"))


class IdentityCapturingModel:
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        assert request.messages[0].role.value == "system"
        assert "Milky Frog" in request.messages[0].content
        assert "奶蛙" in request.messages[0].content
        assert request.messages[1].role.value == "user"
        assert request.messages[1].content == "Who are you?"
        yield TextDelta("I am Milky Frog.")
        yield StreamDone(ModelResponse(content="I am Milky Frog."))


@pytest.mark.asyncio
async def test_harness_runs_tool_loop_and_persists_events(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    harness = Harness(
        model=FakeModel(),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=store,
        handlers=HandlerRegistry(),
    )

    result = await harness.run(RunRequest("echo hello", tmp_path))

    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "done"
    assert result.model_calls == 2
    assert [event.event_type for event in store.events(result.run_id)] == [
        "RunStarted",
        "ModelMessageCompleted",
        "ToolCallRequested",
        "ToolCallCompleted",
        "ModelMessageCompleted",
        "RunCompleted",
    ]


class UsageReportingModel:
    """Reports token usage per call: one tool turn, then a final answer turn."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        self.calls += 1
        if self.calls == 1:
            yield StreamDone(
                ModelResponse(
                    tool_calls=(ToolCall("call-1", "echo", {"text": "hello"}),),
                    usage=TokenUsage(input_tokens=100, output_tokens=20),
                )
            )
            return
        yield StreamDone(
            ModelResponse(
                content="done",
                usage=TokenUsage(input_tokens=160, output_tokens=30, cached_tokens=64),
            )
        )


@pytest.mark.asyncio
async def test_harness_aggregates_token_usage_across_calls(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    harness = Harness(
        model=UsageReportingModel(),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=store,
        handlers=HandlerRegistry(),
    )

    result = await harness.run(RunRequest("echo hello", tmp_path))

    # Cumulative is billed across both calls; context is the final call's input.
    assert result.usage.cumulative == TokenUsage(
        input_tokens=260, output_tokens=50, cached_tokens=64
    )
    assert result.usage.context_tokens == 160

    model_events = [
        e for e in store.events(result.run_id) if e.event_type == "ModelMessageCompleted"
    ]
    assert model_events[0].payload["usage"] == {
        "input_tokens": 100,
        "output_tokens": 20,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 120,
    }


class EarlyStreamDoneModel:
    """Yields StreamDone before trailing chunks to assert early stream exit."""

    def __init__(self) -> None:
        self.extra_chunks_yielded = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        yield StreamDone(ModelResponse(content="done"))
        for index in range(995):
            self.extra_chunks_yielded += 1
            yield TextDelta(f"extra-{index}")


@pytest.mark.asyncio
async def test_harness_stops_model_stream_after_stream_done(tmp_path: Path) -> None:
    model = EarlyStreamDoneModel()
    harness = Harness(
        model=model,
        tools=ToolRegistry(),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        handlers=HandlerRegistry(),
    )

    result = await harness.run(RunRequest("hi", tmp_path))

    assert result.final_message == "done"
    assert model.extra_chunks_yielded == 0


@pytest.mark.asyncio
async def test_harness_persists_reasoning_in_checkpoint(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    harness = Harness(
        model=ReasoningModel(),
        tools=ToolRegistry(),
        checkpoints=store,
        handlers=HandlerRegistry(),
    )

    result = await harness.run(RunRequest("solve it", tmp_path))

    assert result.final_message == "the answer"
    completed = next(
        event
        for event in store.events(result.run_id)
        if event.event_type == "ModelMessageCompleted"
    )
    assert completed.payload["reasoning"] == "weighing options"
    assert completed.payload["content"] == "the answer"


@pytest.mark.asyncio
async def test_harness_injects_milky_frog_identity_before_user_prompt(tmp_path: Path) -> None:
    harness = Harness(
        model=IdentityCapturingModel(),
        tools=ToolRegistry(),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        handlers=HandlerRegistry(),
    )

    result = await harness.run(RunRequest("Who are you?", tmp_path))

    assert result.final_message == "I am Milky Frog."


class CountingEchoTool(EchoTool):
    executions = 0

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        CountingEchoTool.executions += 1
        return await super().execute(context, input)


@pytest.mark.asyncio
async def test_harness_intercept_can_block_tool_execution(tmp_path: Path) -> None:
    CountingEchoTool.executions = 0
    store = SqliteCheckpointStore(tmp_path / "state.db")
    registry = HandlerRegistry()

    @registry.intercept(BeforeTool)
    async def deny(_event: BeforeTool) -> BlockTool:
        return BlockTool("not allowed")

    harness = Harness(
        model=FakeModel(),
        tools=ToolRegistry((CountingEchoTool(),)),
        checkpoints=store,
        handlers=registry,
    )

    result = await harness.run(RunRequest("echo hello", tmp_path))

    assert CountingEchoTool.executions == 0
    assert result.status is RunStatus.COMPLETED
    tool_completed = next(
        event for event in store.events(result.run_id) if event.event_type == "ToolCallCompleted"
    )
    assert tool_completed.payload["content"] == "not allowed"
    assert tool_completed.payload["is_error"] is True


@pytest.mark.asyncio
async def test_harness_intercept_can_transform_model_context(tmp_path: Path) -> None:
    registry = HandlerRegistry()

    @registry.intercept(BeforeModel)
    async def inject_context(event: BeforeModel) -> TransformContext:
        return TransformContext(
            (*event.request.messages, Message(MessageRole.USER, "extra context"))
        )

    class ContextCapturingModel:
        last_user_messages: tuple[str, ...] = ()

        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            ContextCapturingModel.last_user_messages = tuple(
                message.content for message in request.messages if message.role is MessageRole.USER
            )
            yield StreamDone(ModelResponse(content="ok"))

    harness = Harness(
        model=ContextCapturingModel(),
        tools=ToolRegistry(),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        handlers=registry,
    )

    await harness.run(RunRequest("hello", tmp_path))

    assert ContextCapturingModel.last_user_messages == ("hello", "extra context")


@pytest.mark.asyncio
async def test_harness_intercept_can_patch_tool_result(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    registry = HandlerRegistry()

    @registry.intercept(AfterTool)
    async def patch(_event: AfterTool) -> PatchToolResult:
        return PatchToolResult(content="patched")

    harness = Harness(
        model=FakeModel(),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=store,
        handlers=registry,
    )

    result = await harness.run(RunRequest("echo hello", tmp_path))

    tool_completed = next(
        event for event in store.events(result.run_id) if event.event_type == "ToolCallCompleted"
    )
    assert tool_completed.payload["content"] == "patched"


class SlowStreamModel:
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        yield TextDelta("partial")
        await asyncio.sleep(0.05)
        yield StreamDone(ModelResponse(content="done"))


@pytest.mark.asyncio
async def test_harness_dispatches_run_lifecycle_events(tmp_path: Path) -> None:
    registry = HandlerRegistry()
    seen: list[str] = []

    @registry.subscribe
    async def record(event: object) -> None:
        seen.append(type(event).__name__)

    harness = Harness(
        model=FakeModel(),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        handlers=registry,
    )

    result = await harness.run(RunRequest("echo hello", tmp_path))

    assert result.status is RunStatus.COMPLETED
    assert seen[0] == "RunStarted"
    assert seen[-1] == "RunCompleted"
    assert "RunPaused" not in seen
    assert "RunCancelled" not in seen


@pytest.mark.asyncio
async def test_harness_dispatches_run_paused_event(tmp_path: Path) -> None:
    registry = HandlerRegistry()
    paused: list[RunPaused] = []

    @registry.on(RunPaused)
    async def record(event: RunPaused) -> None:
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
async def test_harness_cancellation_stops_run(tmp_path: Path) -> None:
    registry = HandlerRegistry()
    cancelled: list[RunCancelled] = []

    @registry.on(RunCancelled)
    async def record(event: RunCancelled) -> None:
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
    assert any(event.event_type == "RunCancelled" for event in store.events(result.run_id))


@pytest.mark.asyncio
async def test_harness_cancellation_during_tool_execution(tmp_path: Path) -> None:
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
        handlers=HandlerRegistry(),
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
    assert not any(event.event_type == "ToolCallCompleted" for event in store.events(result.run_id))


def test_tool_context_exposes_cancellation_poll() -> None:
    cancellation = RunCancellation()
    context = ToolContext("run-1", Path("."), cancellation)

    assert context.is_cancelled() is False
    cancellation.cancel()
    assert context.is_cancelled() is True


@pytest.mark.asyncio
async def test_run_cancelled_handler_runs_after_checkpoint(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    registry = HandlerRegistry()
    checkpoint_seen = False

    @registry.on(RunCancelled)
    async def record(event: RunCancelled) -> None:
        nonlocal checkpoint_seen
        checkpoint_seen = any(
            item.event_type == "RunCancelled" for item in store.events(event.run_id)
        )

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
async def test_harness_external_cancellation_reraises(tmp_path: Path) -> None:
    harness = Harness(
        model=SlowStreamModel(),
        tools=ToolRegistry(),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        handlers=HandlerRegistry(),
    )

    task = asyncio.create_task(harness.run(RunRequest("slow", tmp_path)))
    await asyncio.sleep(0.01)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_harness_dispatches_run_failed_event(tmp_path: Path) -> None:
    registry = HandlerRegistry()
    failed: list[RunFailed] = []

    @registry.on(RunFailed)
    async def record(event: RunFailed) -> None:
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
    assert any(event.event_type == "RunFailed" for event in store.events(failed[0].run_id))
