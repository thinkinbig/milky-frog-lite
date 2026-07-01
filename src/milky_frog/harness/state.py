from __future__ import annotations

from dataclasses import replace

from milky_frog.domain import Message, MessageRole, ModelResponse, RunState, ToolCall, ToolResult

# Appended as the result of a tool call that was interrupted before completion.
# The model sees the interruption and re-decides; the Tool is never blindly
# re-executed, since its side effects are unknown (see ADR-0002 and ADR-0009).
INTERRUPTED_TOOL_RESULT = (
    "Tool execution was interrupted before it completed; its effect is unknown."
)

__all__ = [
    "INTERRUPTED_TOOL_RESULT",
    "append_model_response",
    "append_tool_result",
    "append_user_message",
    "seal",
    "start_run",
    "unmatched_tool_calls",
]


def start_run(state: RunState, prompt: str) -> RunState:
    # The system prompt is not part of the durable transcript; ContextManager
    # rebuilds it from the Workspace on every model call (see harness/context.py).
    return replace(state, messages=(Message(MessageRole.USER, prompt),))


def append_user_message(state: RunState, content: str) -> RunState:
    return replace(state, messages=(*state.messages, Message(MessageRole.USER, content)))


def append_model_response(state: RunState, response: ModelResponse) -> RunState:
    # Reasoning is intentionally dropped from the transcript: reasoning providers
    # reject their own reasoning_content on input. It survives in reasoning_log.
    return replace(
        state,
        messages=(
            *state.messages,
            Message(
                MessageRole.ASSISTANT,
                response.content,
                response.tool_calls,
            ),
        ),
        completed_model_calls=state.completed_model_calls + 1,
        reasoning_log=(*state.reasoning_log, response.reasoning),
        usage=state.usage.record(response.usage),
    )


def append_tool_result(state: RunState, call: ToolCall, result: ToolResult) -> RunState:
    return replace(
        state,
        messages=(
            *state.messages,
            Message(MessageRole.TOOL, result.content, tool_call_id=call.id),
        ),
    )


def seal(state: RunState) -> tuple[RunState, bool]:
    """Repair a transcript that ends mid-turn so its tail is a valid next request.

    A Run interrupted after a model turn but before every tool result completes
    leaves a trailing assistant message whose tool calls have no result, which most
    providers reject. For each unmatched call, append a synthetic ``is_error`` tool
    result. Returns the sealed state and whether any repair was applied.
    """
    repaired = False
    for call in unmatched_tool_calls(state.messages):
        state = append_tool_result(
            state,
            call,
            ToolResult(INTERRUPTED_TOOL_RESULT, is_error=True),
        )
        repaired = True
    return state, repaired


def unmatched_tool_calls(messages: tuple[Message, ...]) -> tuple[ToolCall, ...]:
    last_assistant = next(
        (
            index
            for index in reversed(range(len(messages)))
            if messages[index].role is MessageRole.ASSISTANT
        ),
        None,
    )
    if last_assistant is None:
        return ()
    assistant = messages[last_assistant]
    if not assistant.tool_calls:
        return ()
    satisfied = {
        message.tool_call_id
        for message in messages[last_assistant + 1 :]
        if message.role is MessageRole.TOOL
    }
    return tuple(call for call in assistant.tool_calls if call.id not in satisfied)
