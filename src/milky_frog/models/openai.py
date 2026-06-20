from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, cast

from openai import AsyncOpenAI, AsyncStream
from openai.types.chat import ChatCompletionChunk

from milky_frog.domain import (
    Message,
    ModelChunk,
    ModelRequest,
    ModelResponse,
    ReasoningDelta,
    StreamDone,
    TextDelta,
    ToolCall,
)


class OpenAIModel:
    """OpenAI-compatible chat-completions adapter for the Harness."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._model = model
        self._client = client or AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        messages = [_message_payload(message) for message in request.messages]
        arguments: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if request.tools:
            arguments["tools"] = list(request.tools)

        stream = cast(
            AsyncStream[ChatCompletionChunk],
            await self._client.chat.completions.create(**arguments),
        )
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_fragments: dict[int, _ToolFragment] = {}
        usage: dict[str, int] = {}

        async for chunk in stream:
            if chunk.usage is not None:
                usage = {
                    "input_tokens": chunk.usage.prompt_tokens,
                    "output_tokens": chunk.usage.completion_tokens,
                    "total_tokens": chunk.usage.total_tokens,
                }
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            # Non-standard field carried by reasoning providers (deepseek-reasoner, …).
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                reasoning_parts.append(reasoning)
                yield ReasoningDelta(reasoning)
            if delta.content:
                content_parts.append(delta.content)
                yield TextDelta(delta.content)
            for call in delta.tool_calls or ():
                fragment = tool_fragments.setdefault(call.index, _ToolFragment())
                if call.id:
                    fragment.id = call.id
                if call.function and call.function.name:
                    fragment.name = call.function.name
                if call.function and call.function.arguments:
                    fragment.arguments += call.function.arguments

        tool_calls = tuple(
            ToolCall(
                id=fragment.id,
                name=fragment.name,
                arguments=_parse_arguments(fragment.arguments),
            )
            for _, fragment in sorted(tool_fragments.items())
        )
        yield StreamDone(
            ModelResponse(
                content="".join(content_parts),
                tool_calls=tool_calls,
                usage=usage,
                reasoning="".join(reasoning_parts),
            )
        )


@dataclass(slots=True)
class _ToolFragment:
    """Accumulates one tool call's pieces across streamed deltas."""

    id: str = ""
    name: str = ""
    arguments: str = field(default="")


def _message_payload(message: Message) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": message.role.value, "content": message.content}
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        payload["tool_call_id"] = message.tool_call_id
    return payload


def _parse_arguments(value: str) -> dict[str, Any]:
    parsed = json.loads(value or "{}")
    if not isinstance(parsed, dict):
        raise ValueError("model tool-call arguments must be a JSON object")
    return parsed
