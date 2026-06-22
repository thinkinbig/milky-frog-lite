from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from milky_frog.domain import (
    Message,
    MessageRole,
    ModelRequest,
    ReasoningDelta,
    StreamDone,
    TextDelta,
    TokenUsage,
)
from milky_frog.models import OpenAIModel


def _chunk(
    *,
    content: str | None = None,
    reasoning: str | None = None,
    tool_calls: list[Any] | None = None,
    usage: Any = None,
):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls, reasoning_content=reasoning)
    empty = content is None and reasoning is None and tool_calls is None and usage is not None
    choices = [] if empty else [SimpleNamespace(delta=delta)]
    return SimpleNamespace(choices=choices, usage=usage)


def _tool_delta(index: int, *, id: str | None = None, name: str | None = None, args: str = ""):
    return SimpleNamespace(
        index=index,
        id=id,
        function=SimpleNamespace(name=name, arguments=args),
    )


class _FakeStream:
    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = chunks
        self.closed = False

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[Any]:
        for chunk in self._chunks:
            yield chunk

    async def close(self) -> None:
        self.closed = True


class _FakeClient:
    def __init__(self, chunks: list[Any]) -> None:
        self.captured: dict[str, Any] = {}
        completions = SimpleNamespace(create=self._create)
        self.chat = SimpleNamespace(completions=completions)
        self._chunks = chunks
        self.last_stream: _FakeStream | None = None

    async def _create(self, **kwargs: Any) -> _FakeStream:
        self.captured = kwargs
        self.last_stream = _FakeStream(self._chunks)
        return self.last_stream


@pytest.mark.asyncio
async def test_stream_forwards_text_and_assembles_response() -> None:
    chunks = [
        _chunk(content="Hel"),
        _chunk(content="lo"),
        _chunk(tool_calls=[_tool_delta(0, id="call-1", name="echo", args='{"text":')]),
        _chunk(tool_calls=[_tool_delta(0, args=' "hi"}')]),
        _chunk(usage=SimpleNamespace(prompt_tokens=3, completion_tokens=5, total_tokens=8)),
    ]
    client = _FakeClient(chunks)
    model = OpenAIModel(api_key="k", model="m", client=client)  # type: ignore[arg-type]
    request = ModelRequest((Message(MessageRole.USER, "hi"),), ())

    deltas: list[str] = []
    done: StreamDone | None = None
    async for chunk in model.stream(request):
        if isinstance(chunk, TextDelta):
            deltas.append(chunk.content)
        else:
            assert isinstance(chunk, StreamDone)
            done = chunk

    assert deltas == ["Hel", "lo"]
    assert done is not None
    assert done.response.content == "Hello"
    assert done.response.tool_calls[0].id == "call-1"
    assert done.response.tool_calls[0].name == "echo"
    assert done.response.tool_calls[0].arguments == {"text": "hi"}
    assert done.response.usage == TokenUsage(input_tokens=3, output_tokens=5)
    assert client.captured["stream"] is True
    assert client.captured["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_stream_surfaces_reasoning_before_answer() -> None:
    chunks = [
        _chunk(reasoning="let me "),
        _chunk(reasoning="think"),
        _chunk(content="answer"),
    ]
    model = OpenAIModel(api_key="k", model="m", client=_FakeClient(chunks))  # type: ignore[arg-type]

    reasoning: list[str] = []
    text: list[str] = []
    done: StreamDone | None = None
    async for chunk in model.stream(ModelRequest((Message(MessageRole.USER, "hi"),), ())):
        if isinstance(chunk, ReasoningDelta):
            reasoning.append(chunk.content)
        elif isinstance(chunk, TextDelta):
            text.append(chunk.content)
        else:
            done = chunk

    assert reasoning == ["let me ", "think"]
    assert text == ["answer"]
    assert done is not None
    assert done.response.reasoning == "let me think"
    assert done.response.content == "answer"


@pytest.mark.asyncio
async def test_stream_omits_stream_options_for_compatible_base_url() -> None:
    client = _FakeClient([_chunk(content="ok")])
    model = OpenAIModel(
        api_key="k",
        model="m",
        base_url="https://example.test/v1",
        client=client,  # type: ignore[arg-type]
    )

    async for _ in model.stream(ModelRequest((Message(MessageRole.USER, "hi"),), ())):
        pass

    assert "stream_options" not in client.captured


@pytest.mark.asyncio
async def test_stream_captures_cached_and_reasoning_subtotals() -> None:
    usage = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=40,
        total_tokens=140,
        prompt_tokens_details=SimpleNamespace(cached_tokens=64),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=30),
    )
    client = _FakeClient([_chunk(content="ok"), _chunk(usage=usage)])
    model = OpenAIModel(api_key="k", model="m", client=client)  # type: ignore[arg-type]

    done: StreamDone | None = None
    async for chunk in model.stream(ModelRequest((Message(MessageRole.USER, "hi"),), ())):
        if isinstance(chunk, StreamDone):
            done = chunk

    assert done is not None
    assert done.response.usage == TokenUsage(
        input_tokens=100, output_tokens=40, cached_tokens=64, reasoning_tokens=30
    )


@pytest.mark.asyncio
async def test_stream_closes_underlying_response_when_consumer_breaks_early() -> None:
    chunks = [
        _chunk(content="partial"),
        _chunk(usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)),
    ]
    client = _FakeClient(chunks)
    model = OpenAIModel(api_key="k", model="m", client=client)  # type: ignore[arg-type]

    async for chunk in model.stream(ModelRequest((Message(MessageRole.USER, "hi"),), ())):
        if isinstance(chunk, StreamDone):
            break

    assert client.last_stream is not None
    assert client.last_stream.closed is True


@pytest.mark.asyncio
async def test_stream_omits_tools_when_none_requested() -> None:
    client = _FakeClient([_chunk(content="ok")])
    model = OpenAIModel(api_key="k", model="m", client=client)  # type: ignore[arg-type]

    async for _ in model.stream(ModelRequest((Message(MessageRole.USER, "hi"),), ())):
        pass

    assert "tools" not in client.captured
