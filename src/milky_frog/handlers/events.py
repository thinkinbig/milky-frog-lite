from __future__ import annotations

from dataclasses import dataclass

from milky_frog.domain import (
    ModelRequest,
    ModelResponse,
    ReasoningDelta,
    RunRequest,
    RunResult,
    RunStatus,
    TextDelta,
    ToolCall,
    ToolResult,
)


@dataclass(frozen=True)
class BaseEvent:
    """Base type for ephemeral Harness lifecycle signals delivered via ``notify``.

    These are not Checkpoint events — they exist only for live UI and
    observability Handlers during a Run.
    """

    run_id: str


@dataclass(frozen=True)
class RunStarted(BaseEvent):
    request: RunRequest


@dataclass(frozen=True)
class RunBeforeModel(BaseEvent):
    request: ModelRequest


@dataclass(frozen=True)
class RunModelReasoning(BaseEvent):
    request: ModelRequest
    chunk: ReasoningDelta


@dataclass(frozen=True)
class RunModelChunk(BaseEvent):
    request: ModelRequest
    chunk: TextDelta


@dataclass(frozen=True)
class RunAfterModel(BaseEvent):
    request: ModelRequest
    response: ModelResponse


@dataclass(frozen=True)
class RunBeforeTool(BaseEvent):
    call: ToolCall


@dataclass(frozen=True)
class RunAfterTool(BaseEvent):
    call: ToolCall
    result: ToolResult


@dataclass(frozen=True)
class RunTurnStart(BaseEvent):
    """Emitted just before each model call in a turn."""

    model_call: int


@dataclass(frozen=True)
class RunTurnEnd(BaseEvent):
    """Emitted after every Tool in a model turn completes, before the next
    model call or terminal outcome."""

    model_call: int


@dataclass(frozen=True)
class RunCompleted(BaseEvent):
    result: RunResult


@dataclass(frozen=True)
class RunPaused(BaseEvent):
    status: RunStatus
    reason: str
    model_calls: int


@dataclass(frozen=True)
class RunCancelled(BaseEvent):
    reason: str
    model_calls: int


@dataclass(frozen=True)
class RunFailed(BaseEvent):
    error: Exception
