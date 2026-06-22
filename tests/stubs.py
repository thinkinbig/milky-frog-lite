from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from pydantic import BaseModel

from milky_frog.checkpoint import CheckpointStore
from milky_frog.domain import (
    ModelChunk,
    ModelRequest,
    ModelResponse,
    ReasoningDelta,
    StreamDone,
    TextDelta,
    TokenUsage,
    ToolCall,
)
from milky_frog.handlers import EventDispatcher
from milky_frog.handlers.checkpoint import CheckpointHandler
from milky_frog.harness.runner import Harness
from milky_frog.harness.tools import ToolContext, ToolRegistry, ToolResult
from milky_frog.models import Model

# ── Harness builder ───────────────────────────────────────────────────


def make_harness(
    model: Model,
    tools: ToolRegistry,
    checkpoints: CheckpointStore,
    handlers: EventDispatcher | None = None,
) -> Harness:
    """Build a Harness with checkpointing wired, mirroring production assembly.

    Production wires ``CheckpointHandler`` via ``handlers.default_handlers``; the
    Harness no longer self-registers it. Tests that need a resumable Run use this
    helper so the snapshot handler lands on the same bus they inspect.
    """
    bus = handlers if handlers is not None else EventDispatcher()
    CheckpointHandler(checkpoints).register(bus)
    return Harness(model, tools, checkpoints, bus)


# ── Tool stubs ────────────────────────────────────────────────────────


class EchoInput(BaseModel):
    text: str


class EchoTool:
    name = "echo"
    description = "Echo text"
    input_model: type[BaseModel] = EchoInput

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        assert context.workspace.is_dir()
        parsed = EchoInput.model_validate(input)
        return ToolResult(parsed.text)


# ── Model stubs ───────────────────────────────────────────────────────


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


class InvalidToolArgsThenRecoverModel:
    """First turn requests a Tool with invalid arguments; second turn sees the error."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        self.calls += 1
        if self.calls == 1:
            yield StreamDone(ModelResponse(tool_calls=(ToolCall("call-1", "echo", {}),)))
            return
        tool_messages = [message for message in request.messages if message.role.value == "tool"]
        assert tool_messages
        assert "ValidationError" in tool_messages[-1].content
        yield StreamDone(ModelResponse(content="recovered"))


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


class EarlyStreamDoneModel:
    """Yields StreamDone before trailing chunks to assert early stream exit."""

    def __init__(self) -> None:
        self.extra_chunks_yielded = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        yield StreamDone(ModelResponse(content="done"))
        for index in range(995):
            self.extra_chunks_yielded += 1
            yield TextDelta(f"extra-{index}")


class SlowStreamModel:
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        yield TextDelta("partial")
        await asyncio.sleep(0.05)
        yield StreamDone(ModelResponse(content="done"))


class PauseThenFinishModel:
    """A tool turn first, then a final answer — to pause at a 1-call budget."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        self.calls += 1
        if self.calls == 1:
            yield StreamDone(
                ModelResponse(tool_calls=(ToolCall("call-1", "echo", {"text": "hi"}),))
            )
            return
        yield StreamDone(ModelResponse(content="done"))


class ContinuationModel:
    """Completes at once, asserting the latest user turn is visible in context."""

    def __init__(self, expected_user: str) -> None:
        self.expected_user = expected_user

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        users = [m for m in request.messages if m.role.value == "user"]
        assert users[-1].content == self.expected_user
        yield StreamDone(ModelResponse(content="ack"))


class FlakyConnectionModel:
    """Fails the first *failures* stream attempts with ``ConnectionError``."""

    def __init__(self, *, failures: int = 2) -> None:
        self._failures_left = failures
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        self.calls += 1
        if self._failures_left > 0:
            self._failures_left -= 1
            raise ConnectionError("offline")
        yield TextDelta("ok")
        yield StreamDone(ModelResponse(content="ok"))


class ImmediateErrorModel:
    """Always raises the configured error on stream."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        self.calls += 1
        raise self._error
        yield StreamDone(ModelResponse())  # makes this an async generator; never reached


# ── Langfuse stubs ───────────────────────────────────────────────────


class LangfuseClientFactory:
    """Stub Langfuse constructor that returns a fixed client."""

    def __init__(self, client: object) -> None:
        self._client = client

    def __call__(self, **kwargs: object) -> object:
        del kwargs
        return self._client
