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
    "append_synthetic_tool_call",
    "append_tool_result",
    "append_user_message",
    "seal",
    "start_run",
    "unmatched_tool_calls",
    "with_run_skills",
]


def start_run(state: RunState, prompt: str) -> RunState:
    # The system prompt is not part of the durable transcript; ContextManager
    # rebuilds it from the Workspace on every model call (see harness/context.py).
    return replace(state, messages=(Message(MessageRole.USER, prompt),))


def append_user_message(state: RunState, content: str) -> RunState:
    return replace(state, messages=(*state.messages, Message(MessageRole.USER, content)))


def with_run_skills(
    state: RunState, run_extra: tuple[str, ...], selected_skills: tuple[str, ...]
) -> RunState:
    """Replace eager Skill instructions and their observable names together.

    Used on resume/continue to re-apply the caller's current Skill selection over
    the persisted value, so mid-run activation and deactivation both take effect —
    and so the injected instructions never diverge from the recorded names.
    """
    return replace(state, run_extra=run_extra, selected_skills=selected_skills)


def append_model_response(state: RunState, response: ModelResponse) -> RunState:
    # Reasoning is intentionally dropped from the transcript: reasoning providers
    # only require it to accompany an assistant Tool call on the next request.
    # Lifecycle Handlers receive all reasoning while streaming, but final-answer
    # reasoning is not retained in the durable RunState.
    return replace(
        state,
        messages=(
            *state.messages,
            Message(
                MessageRole.ASSISTANT,
                response.content,
                response.tool_calls,
                reasoning=response.reasoning if response.tool_calls else "",
            ),
        ),
        completed_model_calls=state.completed_model_calls + 1,
        usage=state.usage.record(response.usage),
    )


def append_synthetic_tool_call(state: RunState, call: ToolCall) -> RunState:
    """Append a harness-synthesized tool call as its own assistant message.

    Not tied to a ``ModelResponse``: no model turn happened, so this does not
    bump ``completed_model_calls`` or record usage. ``unmatched_tool_calls``
    scans the whole transcript, so this call joins any sibling still awaiting
    approval instead of masking it — see that function.
    """
    return replace(
        state,
        messages=(*state.messages, Message(MessageRole.ASSISTANT, "", (call,))),
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
    """Every Tool call in the transcript that still has no matching Tool result.

    Scans the whole transcript rather than just the trailing assistant message.
    A harness-synthesized follow-up (``append_synthetic_tool_call``) lands in an
    assistant message of its own, so a last-message-only scan would hide any
    sibling call from the preceding model turn that is still waiting on
    approval — leaving it permanently undecidable and the transcript malformed
    (an assistant ``tool_calls`` entry with no Tool result, which providers
    reject). Two follow-ups in one batch hid each other the same way.

    Returned in transcript order, so the first entry is the oldest pending call.
    """
    satisfied = {
        message.tool_call_id
        for message in messages
        if message.role is MessageRole.TOOL and message.tool_call_id is not None
    }
    return tuple(
        call
        for message in messages
        if message.role is MessageRole.ASSISTANT
        for call in message.tool_calls
        if call.id not in satisfied
    )
