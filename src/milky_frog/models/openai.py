from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from milky_frog.domain import (
    Message,
    ModelRequest,
    ModelResponse,
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

    async def complete(self, request: ModelRequest) -> ModelResponse:
        messages = [_message_payload(message) for message in request.messages]
        arguments: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
        }
        if request.tools:
            arguments["tools"] = list(request.tools)

        response = await self._client.chat.completions.create(**arguments)
        message = response.choices[0].message
        tool_calls = tuple(
            ToolCall(
                id=call.id,
                name=call.function.name,
                arguments=_parse_arguments(call.function.arguments),
            )
            for call in message.tool_calls or ()
        )
        usage = response.usage
        return ModelResponse(
            content=message.content or "",
            tool_calls=tool_calls,
            usage=(
                {
                    "input_tokens": usage.prompt_tokens,
                    "output_tokens": usage.completion_tokens,
                    "total_tokens": usage.total_tokens,
                }
                if usage is not None
                else {}
            ),
        )


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
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("model tool-call arguments must be a JSON object")
    return parsed
