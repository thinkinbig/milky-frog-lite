from __future__ import annotations

from pathlib import Path

from milky_frog.checkpoint.events import (
    ModelMessageCompletedBody,
    RunCancelledBody,
    RunCompletedBody,
    RunEvent,
    RunFailedBody,
    RunPausedBody,
    RunStartedBody,
    ToolCallCompletedBody,
    ToolCallRequestedBody,
    UserMessageAddedBody,
    token_usage_fields,
    tool_call_fields,
)
from milky_frog.domain import ModelResponse, ToolCall, ToolResult

# Appended as the result of a tool call that was interrupted between its
# ``ToolCallRequested`` and ``ToolCallCompleted`` events. The model sees the
# interruption and re-decides; the Tool is never blindly re-executed, since its
# side effects are unknown (see ADR-0002 and ADR-0009).
INTERRUPTED_TOOL_RESULT = (
    "Tool execution was interrupted before it completed; its effect is unknown."
)


def run_started(*, prompt: str, workspace: Path) -> RunEvent:
    return RunEvent(body=RunStartedBody(prompt=prompt, workspace=str(workspace)))


def user_message_added(content: str) -> RunEvent:
    return RunEvent(body=UserMessageAddedBody(content=content))


def model_message_completed(response: ModelResponse) -> RunEvent:
    return RunEvent(
        body=ModelMessageCompletedBody(
            content=response.content,
            reasoning=response.reasoning,
            tool_calls=tuple(tool_call_fields(call) for call in response.tool_calls),
            usage=token_usage_fields(response.usage),
        )
    )


def tool_call_requested(call: ToolCall) -> RunEvent:
    fields = tool_call_fields(call)
    return RunEvent(
        body=ToolCallRequestedBody(
            id=fields.id,
            name=fields.name,
            arguments=fields.arguments,
        )
    )


def tool_call_completed(call: ToolCall, result: ToolResult) -> RunEvent:
    fields = tool_call_fields(call)
    return RunEvent(
        body=ToolCallCompletedBody(
            id=fields.id,
            name=fields.name,
            content=result.content,
            is_error=result.is_error,
        )
    )


def interrupted_tool_call_completed(call: ToolCall) -> RunEvent:
    return tool_call_completed(call, ToolResult(INTERRUPTED_TOOL_RESULT, is_error=True))


def run_completed(*, final_message: str) -> RunEvent:
    return RunEvent(body=RunCompletedBody(final_message=final_message))


def run_paused(*, reason: str, model_calls: int) -> RunEvent:
    return RunEvent(body=RunPausedBody(reason=reason, model_calls=model_calls))


def run_cancelled(*, reason: str, model_calls: int) -> RunEvent:
    return RunEvent(body=RunCancelledBody(reason=reason, model_calls=model_calls))


def run_failed(error: BaseException) -> RunEvent:
    return RunEvent(
        body=RunFailedBody(error_type=type(error).__name__, message=str(error)),
    )
