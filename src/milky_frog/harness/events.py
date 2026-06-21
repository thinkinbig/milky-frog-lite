from __future__ import annotations

from pathlib import Path

from pydantic import JsonValue

from milky_frog.checkpoint import RunEvent
from milky_frog.domain import (
    Message,
    MessageRole,
    ModelResponse,
    TokenUsage,
    ToolCall,
    ToolResult,
)
from milky_frog.harness.prompt import system_prompt

# Appended as the result of a tool call that was interrupted between its
# ``ToolCallRequested`` and ``ToolCallCompleted`` events. The model sees the
# interruption and re-decides; the Tool is never blindly re-executed, since its
# side effects are unknown (see ADR-0002 and ADR-0009).
INTERRUPTED_TOOL_RESULT = (
    "Tool execution was interrupted before it completed; its effect is unknown."
)


def run_started(*, prompt: str, workspace: Path) -> RunEvent:
    return RunEvent(
        "RunStarted",
        {"prompt": prompt, "workspace": str(workspace)},
    )


def user_message_added(content: str) -> RunEvent:
    return RunEvent("UserMessageAdded", {"content": content})


def model_message_completed(response: ModelResponse) -> RunEvent:
    return RunEvent(
        "ModelMessageCompleted",
        {
            "content": response.content,
            "reasoning": response.reasoning,
            "tool_calls": [_tool_call_payload(call) for call in response.tool_calls],
            "usage": _usage_payload(response.usage),
        },
    )


def tool_call_requested(call: ToolCall) -> RunEvent:
    return RunEvent("ToolCallRequested", _tool_call_payload(call))


def tool_call_completed(call: ToolCall, result: ToolResult) -> RunEvent:
    return RunEvent(
        "ToolCallCompleted",
        {
            "id": call.id,
            "name": call.name,
            "content": result.content,
            "is_error": result.is_error,
        },
    )


def interrupted_tool_call_completed(call: ToolCall) -> RunEvent:
    return tool_call_completed(call, ToolResult(INTERRUPTED_TOOL_RESULT, is_error=True))


def run_completed(*, final_message: str) -> RunEvent:
    return RunEvent("RunCompleted", {"final_message": final_message})


def run_paused(*, reason: str, model_calls: int) -> RunEvent:
    return RunEvent("RunPaused", {"reason": reason, "model_calls": model_calls})


def run_cancelled(*, reason: str, model_calls: int) -> RunEvent:
    return RunEvent("RunCancelled", {"reason": reason, "model_calls": model_calls})


def run_failed(error: BaseException) -> RunEvent:
    return RunEvent(
        "RunFailed",
        {"error_type": type(error).__name__, "message": str(error)},
    )


def seed_messages(workspace: Path, payload: dict[str, JsonValue]) -> tuple[Message, ...]:
    return (
        Message(MessageRole.SYSTEM, system_prompt(workspace)),
        Message(MessageRole.USER, _as_str(payload.get("prompt"))),
    )


def user_message(payload: dict[str, JsonValue]) -> Message:
    return Message(MessageRole.USER, _as_str(payload.get("content")))


def assistant_message(payload: dict[str, JsonValue]) -> Message:
    # Reasoning is intentionally dropped from the transcript: reasoning providers
    # reject their own reasoning_content on input. It survives in the Checkpoint.
    return Message(
        MessageRole.ASSISTANT,
        _as_str(payload.get("content")),
        _tool_calls(payload.get("tool_calls")),
    )


def tool_message(payload: dict[str, JsonValue]) -> Message:
    return Message(
        MessageRole.TOOL,
        _as_str(payload.get("content")),
        tool_call_id=_as_str(payload.get("id")),
    )


def usage_from_payload(value: JsonValue) -> TokenUsage:
    if not isinstance(value, dict):
        return TokenUsage()
    return TokenUsage(
        input_tokens=_as_int(value.get("input_tokens")),
        output_tokens=_as_int(value.get("output_tokens")),
        cached_tokens=_as_int(value.get("cached_tokens")),
        reasoning_tokens=_as_int(value.get("reasoning_tokens")),
    )


def _usage_payload(usage: TokenUsage) -> dict[str, JsonValue]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cached_tokens": usage.cached_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
        "total_tokens": usage.total_tokens,
    }


def _tool_call_payload(call: ToolCall) -> dict[str, JsonValue]:
    return {"id": call.id, "name": call.name, "arguments": call.arguments}


def _tool_calls(value: JsonValue) -> tuple[ToolCall, ...]:
    if not isinstance(value, list):
        return ()
    calls: list[ToolCall] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        arguments = item.get("arguments")
        calls.append(
            ToolCall(
                _as_str(item.get("id")),
                _as_str(item.get("name")),
                arguments if isinstance(arguments, dict) else {},
            )
        )
    return tuple(calls)


def _as_str(value: JsonValue) -> str:
    return value if isinstance(value, str) else ""


def _as_int(value: JsonValue) -> int:
    return value if isinstance(value, int) else 0
