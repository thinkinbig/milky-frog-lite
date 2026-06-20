from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic import BaseModel

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.domain import (
    ModelChunk,
    ModelRequest,
    ModelResponse,
    ReasoningDelta,
    RunRequest,
    RunStatus,
    StreamDone,
    TextDelta,
    ToolCall,
)
from milky_frog.handlers import HandlerRegistry
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
        assert request.messages[-1].content == "hello"
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
