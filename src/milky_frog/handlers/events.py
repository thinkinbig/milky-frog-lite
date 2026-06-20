from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from milky_frog.domain import ModelRequest, ModelResponse, ToolCall

if TYPE_CHECKING:
    from milky_frog.harness.tools import ToolResult


@dataclass(slots=True)
class BeforeModel:
    run_id: str
    request: ModelRequest


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
