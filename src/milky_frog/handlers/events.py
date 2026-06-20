from __future__ import annotations

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
from milky_frog.handlers.base import BaseEvent


class RunStarted(BaseEvent):
    request: RunRequest


class RunCompleted(BaseEvent):
    result: RunResult


class RunPaused(BaseEvent):
    status: RunStatus
    reason: str
    model_calls: int


class RunCancelled(BaseEvent):
    reason: str
    model_calls: int


class BeforeModel(BaseEvent):
    request: ModelRequest


class OnModelReasoning(BaseEvent):
    request: ModelRequest
    chunk: ReasoningDelta


class OnModelChunk(BaseEvent):
    request: ModelRequest
    chunk: TextDelta


class AfterModel(BaseEvent):
    request: ModelRequest
    response: ModelResponse


class BeforeTool(BaseEvent):
    call: ToolCall


class AfterTool(BaseEvent):
    call: ToolCall
    result: ToolResult


class RunFailed(BaseEvent):
    error: Exception
