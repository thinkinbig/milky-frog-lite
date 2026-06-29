from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from milky_frog.core.sandbox import SandboxViolation
from milky_frog.domain import ToolResult
from milky_frog.harness.tools.base import ToolContext
from milky_frog.harness.tools.truncate import truncate_tool_output


class _DirectoryEntryOrder:
    def __call__(self, path: Path) -> tuple[bool, str]:
        return (not path.is_dir(), path.name)


def render_directory(resolved: Path) -> str:
    """Render a directory's entries one per line, with a trailing slash on subdirectories.

    Shared by ``ListDirTool`` and ``read_file``'s directory-degrade path so both
    produce identical listings. May raise ``OSError`` — callers handle it.
    """
    entries = sorted(resolved.iterdir(), key=_DirectoryEntryOrder())
    if not entries:
        return "(empty directory)"
    return "\n".join(f"{entry.name}/" if entry.is_dir() else entry.name for entry in entries)


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
        sandbox = context.require_sandbox()
        try:
            resolved = sandbox.resolve(params.path)
        except SandboxViolation as error:
            return ToolResult(str(error), is_error=True)
        if not resolved.is_dir():
            return ToolResult(f"not a directory: {params.path}", is_error=True)
        try:
            text = render_directory(resolved)
        except OSError as error:
            return ToolResult(f"{type(error).__name__}: {error}", is_error=True)

        text = truncate_tool_output(
            text,
            max_chars=sandbox.config.search_output_max_chars,
            workspace=sandbox.workspace,
            label="list_dir",
        )

        return ToolResult(text)
