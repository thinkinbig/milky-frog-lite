from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from milky_frog.domain import RunCancellation, ToolResult
from milky_frog.harness.execution_backend import ExecutionBackend, LocalExecutionBackend


@dataclass(frozen=True, slots=True)
class ToolContext:
    run_id: str
    workspace: Path
    cancellation: RunCancellation | None = None
    backend: ExecutionBackend | None = None

    def is_cancelled(self) -> bool:
        return self.cancellation is not None and self.cancellation.is_cancelled

    def require_backend(self) -> ExecutionBackend:
        """Return the execution backend, building a default for the Workspace if absent."""
        return self.backend or LocalExecutionBackend(self.workspace)


class Tool(Protocol):
    name: str
    description: str
    input_model: type[BaseModel]

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult: ...
