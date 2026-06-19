from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class ToolContext:
    run_id: str
    workspace: Path


@dataclass(frozen=True, slots=True)
class ToolResult:
    content: str
    is_error: bool = False


class Tool(Protocol):
    name: str
    description: str
    input_model: type[BaseModel]

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult: ...
