from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from milky_frog.domain import ToolResult
from milky_frog.harness.execution_backend import SandboxViolation
from milky_frog.harness.tools.base import ToolContext
from milky_frog.harness.tools.truncate import truncate_tool_output


class _DirectoryEntryOrder:
    def __call__(self, path: Path) -> tuple[bool, str]:
        return (not path.is_dir(), path.name)


class ListDirInput(BaseModel):
    path: str = Field(default=".", description="Workspace-relative directory to list.")


class ListDirTool:
    """List the entries of a Workspace directory."""

    name = "list_dir"
    requires_approval = False
    description = (
        "List the entries of a workspace directory, one per line, with a trailing slash on "
        "subdirectories. Defaults to the workspace root. The path must stay inside the "
        "workspace; sensitive paths are denied."
    )
    input_model: type[BaseModel] = ListDirInput

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        params = ListDirInput.model_validate(input)
        backend = context.require_backend()
        try:
            resolved = backend.resolve(params.path)
        except SandboxViolation as error:
            return ToolResult(str(error), is_error=True)
        if not resolved.is_dir():
            return ToolResult(f"not a directory: {params.path}", is_error=True)
        try:
            entries = sorted(resolved.iterdir(), key=_DirectoryEntryOrder())
        except OSError as error:
            return ToolResult(f"{type(error).__name__}: {error}", is_error=True)
        if not entries:
            return ToolResult("(empty directory)")
        lines = [f"{entry.name}/" if entry.is_dir() else entry.name for entry in entries]
        text = "\n".join(lines)

        text = truncate_tool_output(text, max_chars=32000, tool_name="list_dir")

        return ToolResult(text)
