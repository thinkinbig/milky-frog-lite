from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from milky_frog.domain import (
    CompactionState,
    Message,
    MessageRole,
    RunKind,
    RunState,
    RunUsage,
    TokenUsage,
    ToolCall,
)

SNAPSHOT_VERSION = 1


class ToolCallSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = ""
    name: str = ""
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class MessageSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: str
    content: str = ""
    tool_calls: tuple[ToolCallSnapshot, ...] = ()
    tool_call_id: str | None = None
    reasoning: str | None = None


class TokenUsageSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0


class RunUsageSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    cumulative: TokenUsageSnapshot = Field(default_factory=TokenUsageSnapshot)
    context_tokens: int = 0


class CompactionSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    summary: str
    through_index: int


class RunSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: int = SNAPSHOT_VERSION
    messages: tuple[MessageSnapshot, ...] = ()
    completed_model_calls: int = 0
    # Prior snapshot versions stored one reasoning entry per model response at
    # the root. Read it for migration only; new snapshots keep only reasoning
    # attached to assistant messages that carry Tool calls.
    legacy_reasoning_log: tuple[str, ...] = Field(
        default=(), validation_alias="reasoning_log", exclude=True
    )
    usage: RunUsageSnapshot = Field(default_factory=RunUsageSnapshot)
    compaction: CompactionSnapshot | None = None
    # ``run_extra`` carries eager system-prompt sections injected at Run start
    # (e.g. activated skill instructions). It is durable so that ``resume`` /
    # ``continue_with`` see the same prompts across every turn (see ADR-0014).
    run_extra: tuple[str, ...] = ()
    selected_skills: tuple[str, ...] = ()
    run_kind: RunKind = "foreground"
    parent_run_id: str | None = None


def dump_run_state(state: RunState) -> str:
    snapshot = RunSnapshot(
        messages=tuple(_message_to_snapshot(message) for message in state.messages),
        completed_model_calls=state.completed_model_calls,
        usage=_usage_to_snapshot(state.usage),
        compaction=_compaction_to_snapshot(state.compaction),
        run_extra=state.run_extra,
        selected_skills=state.selected_skills,
        run_kind=state.run_kind,
        parent_run_id=state.parent_run_id,
    )
    return snapshot.model_dump_json()


def load_run_state(run_id: str, workspace: Path, raw: str) -> RunState:
    snapshot = RunSnapshot.model_validate_json(raw)
    return RunState(
        run_id=run_id,
        workspace=workspace,
        # System prompts are no longer part of the transcript (ContextManager
        # rebuilds them per call); drop any persisted by older snapshots.
        messages=_messages_from_snapshot(snapshot),
        completed_model_calls=snapshot.completed_model_calls,
        usage=_usage_from_snapshot(snapshot.usage),
        compaction=_compaction_from_snapshot(snapshot.compaction),
        run_extra=snapshot.run_extra,
        selected_skills=snapshot.selected_skills,
        run_kind=snapshot.run_kind,
        parent_run_id=snapshot.parent_run_id,
    )


def _compaction_to_snapshot(compaction: CompactionState | None) -> CompactionSnapshot | None:
    if compaction is None:
        return None
    return CompactionSnapshot(summary=compaction.summary, through_index=compaction.through_index)


def _compaction_from_snapshot(snapshot: CompactionSnapshot | None) -> CompactionState | None:
    if snapshot is None:
        return None
    return CompactionState(summary=snapshot.summary, through_index=snapshot.through_index)


def _messages_from_snapshot(snapshot: RunSnapshot) -> tuple[Message, ...]:
    messages: list[Message] = []
    assistant_count = 0
    for message in snapshot.messages:
        legacy_reasoning = ""
        if message.role == MessageRole.ASSISTANT.value:
            if assistant_count < len(snapshot.legacy_reasoning_log):
                legacy_reasoning = snapshot.legacy_reasoning_log[assistant_count]
            assistant_count += 1
        if message.role != MessageRole.SYSTEM.value:
            messages.append(_message_from_snapshot(message, legacy_reasoning=legacy_reasoning))
    return tuple(messages)


def _message_to_snapshot(message: Message) -> MessageSnapshot:
    return MessageSnapshot(
        role=message.role.value,
        content=message.content,
        tool_calls=tuple(
            ToolCallSnapshot(id=call.id, name=call.name, arguments=call.arguments)
            for call in message.tool_calls
        ),
        tool_call_id=message.tool_call_id,
        reasoning=message.reasoning or None,
    )


def _message_from_snapshot(message: MessageSnapshot, *, legacy_reasoning: str = "") -> Message:
    return Message(
        role=MessageRole(message.role),
        content=message.content,
        tool_calls=tuple(
            ToolCall(id=call.id, name=call.name, arguments=call.arguments)
            for call in message.tool_calls
        ),
        tool_call_id=message.tool_call_id,
        reasoning=message.reasoning or (legacy_reasoning if message.tool_calls else ""),
    )


def _usage_to_snapshot(usage: RunUsage) -> RunUsageSnapshot:
    cumulative = usage.cumulative
    return RunUsageSnapshot(
        cumulative=TokenUsageSnapshot(
            input_tokens=cumulative.input_tokens,
            output_tokens=cumulative.output_tokens,
            cached_tokens=cumulative.cached_tokens,
            reasoning_tokens=cumulative.reasoning_tokens,
        ),
        context_tokens=usage.context_tokens,
    )


def _usage_from_snapshot(usage: RunUsageSnapshot) -> RunUsage:
    cumulative = usage.cumulative
    return RunUsage(
        cumulative=TokenUsage(
            input_tokens=cumulative.input_tokens,
            output_tokens=cumulative.output_tokens,
            cached_tokens=cumulative.cached_tokens,
            reasoning_tokens=cumulative.reasoning_tokens,
        ),
        context_tokens=usage.context_tokens,
    )
