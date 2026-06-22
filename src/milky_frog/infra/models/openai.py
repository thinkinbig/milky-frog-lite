from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterator
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
    TokenUsage,
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
        include_stream_usage: bool | None = None,
    ) -> None:
        self._model = model
        self._client = client or AsyncOpenAI(api_key=api_key, base_url=base_url)
        # Official OpenAI supports stream usage; many compatible gateways reject it.
        self._include_stream_usage = (
            include_stream_usage if include_stream_usage is not None else base_url is None
        )

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk, None]:
        messages = [_message_payload(message) for message in request.messages]
        arguments: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": True,
        }
        if self._include_stream_usage:
            arguments["stream_options"] = {"include_usage": True}
        if request.tools:
            arguments["tools"] = list(request.tools)

        stream = cast(
            AsyncStream[ChatCompletionChunk],
            await self._client.chat.completions.create(**arguments),
        )
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_fragments: dict[int, _ToolFragment] = {}
        usage = TokenUsage()

        try:
            async for chunk in stream:
                if chunk.usage is not None:
                    usage = _token_usage(chunk.usage)
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
                    model=self._model,
                    reasoning="".join(reasoning_parts),
                )
            )
        finally:
            await stream.close()


def _token_usage(usage: Any) -> TokenUsage:
    """Map an OpenAI ``CompletionUsage`` to the domain ``TokenUsage``.

    Cached-prompt and reasoning sub-totals live on optional ``*_details``
    sub-objects that compatible gateways often omit, so both are read
    defensively and default to zero.
    """
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    completion_details = getattr(usage, "completion_tokens_details", None)
    return TokenUsage(
        input_tokens=usage.prompt_tokens or 0,
        output_tokens=usage.completion_tokens or 0,
        cached_tokens=getattr(prompt_details, "cached_tokens", 0) or 0,
        reasoning_tokens=getattr(completion_details, "reasoning_tokens", 0) or 0,
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
