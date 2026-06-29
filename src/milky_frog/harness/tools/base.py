from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from milky_frog.adapters.local.sandbox import LocalSandbox
from milky_frog.core.sandbox import Sandbox
from milky_frog.domain import RunCancellation, ToolResult
from milky_frog.tokens import TokenCounter


@dataclass(frozen=True, slots=True)
class ToolContext:
    run_id: str
    workspace: Path
    cancellation: RunCancellation | None = None
    sandbox: Sandbox | None = None
    token_counter: TokenCounter | None = None

    def is_cancelled(self) -> bool:
        return self.cancellation is not None and self.cancellation.is_cancelled

    def require_sandbox(self) -> Sandbox:
        """Return the sandbox, building a default for the Workspace if absent."""
        return self.sandbox or LocalSandbox(self.workspace)


class Tool(Protocol):
    name: str
    description: str
    input_model: type[BaseModel]

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult: ...
