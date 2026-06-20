from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from milky_frog.domain import ModelRequest, ModelResponse, ReasoningDelta, TextDelta, ToolCall

if TYPE_CHECKING:
    from milky_frog.harness.tools import ToolResult


@dataclass(slots=True)
class BeforeModel:
    run_id: str
    request: ModelRequest


@dataclass(slots=True)
class OnModelReasoning:
    run_id: str
    request: ModelRequest
    chunk: ReasoningDelta


@dataclass(slots=True)
class OnModelChunk:
    run_id: str
    request: ModelRequest
    chunk: TextDelta


@dataclass(slots=True)
class AfterModel:
    run_id: str
    request: ModelRequest
    response: ModelResponse


@dataclass(slots=True)
class BeforeTool:
    run_id: str
    call: ToolCall


@dataclass(slots=True)
class AfterTool:
    run_id: str
    call: ToolCall
    result: ToolResult


@dataclass(slots=True)
class RunFailed:
    run_id: str
    error: Exception


@dataclass(slots=True)
class OnModelChunk:
    run_id: str
    chunk: TextDelta


@dataclass(slots=True)
class OnModelReasoning:
    run_id: str
    chunk: ReasoningDelta
