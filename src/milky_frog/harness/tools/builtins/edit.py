from __future__ import annotations

from pydantic import BaseModel, Field

from milky_frog.domain import ToolResult
from milky_frog.harness.sandbox import SandboxViolation
from milky_frog.harness.tools.base import ToolContext


class EditFileInput(BaseModel):
    path: str = Field(description="Workspace-relative path to the file to edit.")
    old: str = Field(description="Exact text to replace; must occur exactly once.")
    new: str = Field(description="Replacement text.")


class EditFileTool:
    """Replace the unique occurrence of a string in a Workspace file."""

    name = "edit_file"
    requires_approval = True
    description = (
        "Replace an exact string in a workspace text file. `old` must appear exactly once "
        "(include enough surrounding context to make it unique); it is replaced with `new`. "
        "The path must stay inside the workspace; sensitive paths are denied."
    )
    input_model: type[BaseModel] = EditFileInput

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        params = EditFileInput.model_validate(input)
        if params.old == params.new:
            return ToolResult("old and new are identical; nothing to change", is_error=True)
        sandbox = context.require_sandbox()
        try:
            resolved = sandbox.resolve(params.path)
        except SandboxViolation as error:
            return ToolResult(str(error), is_error=True)
        if not resolved.is_file():
            return ToolResult(f"not a file: {params.path}", is_error=True)
        try:
            content = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            return ToolResult(f"{type(error).__name__}: {error}", is_error=True)
        occurrences = content.count(params.old)
        if occurrences == 0:
            return ToolResult(f"`old` not found in {params.path}", is_error=True)
        if occurrences > 1:
            return ToolResult(
                f"`old` is not unique in {params.path} ({occurrences} matches); "
                "add surrounding context",
                is_error=True,
            )
        try:
            resolved.write_text(content.replace(params.old, params.new), encoding="utf-8")
        except OSError as error:
            return ToolResult(f"{type(error).__name__}: {error}", is_error=True)
        return ToolResult(f"edited {params.path}")
