from pathlib import Path

import pytest
from pydantic import BaseModel

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.domain import ModelRequest, ModelResponse, RunRequest, RunStatus, ToolCall
from milky_frog.handlers import HandlerRegistry
from milky_frog.harness import Harness
from milky_frog.tools import ToolContext, ToolRegistry, ToolResult


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

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.calls == 1:
            assert request.tools[0]["function"]["name"] == "echo"
            return ModelResponse(tool_calls=(ToolCall("call-1", "echo", {"text": "hello"}),))
        assert request.messages[-1].content == "hello"
        return ModelResponse(content="done")


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
