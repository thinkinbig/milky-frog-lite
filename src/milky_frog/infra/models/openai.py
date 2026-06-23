from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Self, cast

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

_logger = logging.getLogger(__name__)


class OpenAIModel:
    """OpenAI-compatible chat-completions adapter for the Harness.

    Configuration is fixed at construction; the HTTP client is acquired on
    ``async with`` and released on exit. Pass ``client=`` only in tests to
    inject a fake client (it is not closed by :meth:`aclose`).
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        client: AsyncOpenAI | None = None,
        include_stream_usage: bool | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url
        self._injected_client = client
        self._client: AsyncOpenAI | None = None
        # Official OpenAI supports stream usage; many compatible gateways reject it.
        self._include_stream_usage = (
            include_stream_usage if include_stream_usage is not None else base_url is None
        )

    async def __aenter__(self) -> Self:
        if self._client is None:
            if self._injected_client is not None:
                self._client = self._injected_client
            else:
                self._client = AsyncOpenAI(api_key=self._api_key, base_url=self._base_url)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            await self.aclose()
        except Exception:
            _logger.exception("Cleanup failed: %s", type(self).__qualname__)

    async def aclose(self) -> None:
        """Close an owned HTTP client and its connection pool.

        Injected test clients are left open.
        """
        if self._client is None or self._injected_client is not None:
            self._client = None
            return
        await self._client.close()
        self._client = None

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk, None]:
        client = self._require_client()
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
            await client.chat.completions.create(**arguments),
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

    def _require_client(self) -> AsyncOpenAI:
        if self._client is None:
            msg = "OpenAIModel must be entered with `async with` before use"
            raise RuntimeError(msg)
        return self._client


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
