from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from milky_frog.domain import TokenUsage, ToolCall


class ToolCallFields(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = ""
    name: str = ""
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class TokenUsageFields(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0


class RunStartedBody(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_type: Literal["RunStarted"] = "RunStarted"
    prompt: str
    workspace: str


class UserMessageAddedBody(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_type: Literal["UserMessageAdded"] = "UserMessageAdded"
    content: str


class ModelMessageCompletedBody(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_type: Literal["ModelMessageCompleted"] = "ModelMessageCompleted"
    content: str = ""
    reasoning: str = ""
    tool_calls: tuple[ToolCallFields, ...] = ()
    usage: TokenUsageFields = Field(default_factory=TokenUsageFields)


class ToolCallRequestedBody(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_type: Literal["ToolCallRequested"] = "ToolCallRequested"
    id: str
    name: str
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class ToolCallCompletedBody(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_type: Literal["ToolCallCompleted"] = "ToolCallCompleted"
    id: str
    name: str
    content: str
    is_error: bool = False


class RunCompletedBody(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_type: Literal["RunCompleted"] = "RunCompleted"
    final_message: str


class RunPausedBody(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_type: Literal["RunPaused"] = "RunPaused"
    reason: str
    model_calls: int


class RunCancelledBody(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_type: Literal["RunCancelled"] = "RunCancelled"
    reason: str
    model_calls: int


class RunFailedBody(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_type: Literal["RunFailed"] = "RunFailed"
    error_type: str
    message: str


CheckpointBody = Annotated[
    RunStartedBody
    | UserMessageAddedBody
    | ModelMessageCompletedBody
    | ToolCallRequestedBody
    | ToolCallCompletedBody
    | RunCompletedBody
    | RunPausedBody
    | RunCancelledBody
    | RunFailedBody,
    Field(discriminator="event_type"),
]

_BODY_ADAPTER: TypeAdapter[CheckpointBody] = TypeAdapter(CheckpointBody)


class RunEvent(BaseModel):
    """One append-only Checkpoint record: typed body plus store metadata."""

    model_config = ConfigDict(frozen=True)

    body: CheckpointBody
    version: int = 1
    sequence: int | None = None
    created_at: datetime | None = None

    @property
    def event_type(self) -> str:
        return self.body.event_type

    @property
    def payload(self) -> dict[str, JsonValue]:
        _, payload = dump_checkpoint_body(self.body)
        return payload

    @classmethod
    def from_parts(
        cls,
        event_type: str,
        payload: dict[str, JsonValue],
        *,
        version: int = 1,
        sequence: int | None = None,
        created_at: datetime | None = None,
    ) -> RunEvent:
        return cls(
            body=load_checkpoint_body(event_type, payload),
            version=version,
            sequence=sequence,
            created_at=created_at,
        )


def load_checkpoint_body(event_type: str, payload: dict[str, JsonValue]) -> CheckpointBody:
    return _BODY_ADAPTER.validate_python({"event_type": event_type, **payload})


def dump_checkpoint_body(body: CheckpointBody) -> tuple[str, dict[str, JsonValue]]:
    raw = body.model_dump(mode="json")
    event_type = raw.pop("event_type")
    if not isinstance(event_type, str):
        raise ValueError("checkpoint event body must include event_type")
    return event_type, raw


def tool_call_from_fields(fields: ToolCallFields) -> ToolCall:
    return ToolCall(fields.id, fields.name, fields.arguments)


def token_usage_from_fields(fields: TokenUsageFields) -> TokenUsage:
    return TokenUsage(
        input_tokens=fields.input_tokens,
        output_tokens=fields.output_tokens,
        cached_tokens=fields.cached_tokens,
        reasoning_tokens=fields.reasoning_tokens,
    )


def tool_call_fields(call: ToolCall) -> ToolCallFields:
    return ToolCallFields(id=call.id, name=call.name, arguments=call.arguments)


def token_usage_fields(usage: TokenUsage) -> TokenUsageFields:
    return TokenUsageFields(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_tokens=usage.cached_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        total_tokens=usage.total_tokens,
    )
