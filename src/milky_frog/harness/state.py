from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

from milky_frog.checkpoint import RunEvent
from milky_frog.domain import Message, MessageRole, RunState, ToolCall
from milky_frog.harness.events import (
    INTERRUPTED_TOOL_RESULT,
    assistant_message,
    interrupted_tool_call_completed,
    seed_messages,
    tool_message,
    usage_from_payload,
    user_message,
)

__all__ = ["INTERRUPTED_TOOL_RESULT", "fold", "reduce", "seal"]


def reduce(state: RunState, event: RunEvent) -> RunState:
    """Fold one Checkpoint event into a ``RunState``.

    The sole writer of a Run's transcript: the live loop calls it as each event
    is emitted, and ``fold`` calls it while replaying a persisted log. Events
    that do not change the transcript (``ToolCallRequested``, terminal markers)
    are returned unchanged.
    """
    if event.event_type == "RunStarted":
        return replace(state, messages=seed_messages(state.workspace, event.payload))
    if event.event_type == "UserMessageAdded":
        return replace(state, messages=(*state.messages, user_message(event.payload)))
    if event.event_type == "ModelMessageCompleted":
        return replace(
            state,
            messages=(*state.messages, assistant_message(event.payload)),
            completed_model_calls=state.completed_model_calls + 1,
            usage=state.usage.record(usage_from_payload(event.payload.get("usage"))),
        )
    if event.event_type == "ToolCallCompleted":
        return replace(state, messages=(*state.messages, tool_message(event.payload)))
    return state


def fold(run_id: str, workspace: Path, events: Iterable[RunEvent]) -> RunState:
    """Replay a Run's persisted events into a ``RunState``."""
    state = RunState(run_id=run_id, workspace=workspace)
    for event in events:
        state = reduce(state, event)
    return state


def seal(state: RunState) -> tuple[RunState, tuple[RunEvent, ...]]:
    """Repair a transcript that ends mid-turn so its tail is a valid next request.

    A Run interrupted between ``ToolCallRequested`` and ``ToolCallCompleted``
    folds to a trailing assistant message whose tool calls have no result, which
    most providers reject. For each unmatched call, append a synthetic
    ``is_error`` ``ToolCallCompleted`` — a real, durable event — and fold it in.
    Returns the sealed state and the repair events to persist (empty when the
    transcript already ends on a clean boundary).
    """
    repairs: list[RunEvent] = []
    for call in _unmatched_tool_calls(state.messages):
        event = interrupted_tool_call_completed(call)
        repairs.append(event)
        state = reduce(state, event)
    return state, tuple(repairs)


def _unmatched_tool_calls(messages: tuple[Message, ...]) -> tuple[ToolCall, ...]:
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
