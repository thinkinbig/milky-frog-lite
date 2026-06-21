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
class BeforeModel(BaseEvent):
    request: ModelRequest


@dataclass(frozen=True)
class OnModelReasoning(BaseEvent):
    request: ModelRequest
    chunk: ReasoningDelta


@dataclass(frozen=True)
class OnModelChunk(BaseEvent):
    request: ModelRequest
    chunk: TextDelta


@dataclass(frozen=True)
class AfterModel(BaseEvent):
    request: ModelRequest
    response: ModelResponse


@dataclass(frozen=True)
class BeforeTool(BaseEvent):
    call: ToolCall


@dataclass(frozen=True)
class AfterTool(BaseEvent):
    call: ToolCall
    result: ToolResult


@dataclass(frozen=True)
class RunFailed(BaseEvent):
    error: Exception
