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
    search_prefix: str = ""

    def is_cancelled(self) -> bool:
        return self.cancellation is not None and self.cancellation.is_cancelled

    def require_sandbox(self) -> Sandbox:
        """Return the sandbox, building a default for the Workspace if absent."""
        return self.sandbox or LocalSandbox(self.workspace)

    def make_output_path(self, relative_to_search: str) -> str:
        """Convert a path relative to the current search scope to workspace-relative format.

        If search_prefix is set (e.g., "src"), prepend it to make the path
        workspace-relative. If search_prefix is empty or ".", return as-is.
        """
        if self.search_prefix and self.search_prefix != ".":
            return f"{self.search_prefix}/{relative_to_search}"
        return relative_to_search


class Tool(Protocol):
    name: str
    description: str
    input_model: type[BaseModel]

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult: ...
