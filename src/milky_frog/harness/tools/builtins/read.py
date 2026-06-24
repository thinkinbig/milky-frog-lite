from __future__ import annotations

from pydantic import BaseModel, Field

from milky_frog.domain import ToolResult
from milky_frog.harness.sandbox import SandboxViolation
from milky_frog.harness.tools.base import ToolContext


class ReadFileInput(BaseModel):
    path: str = Field(description="Workspace-relative path to the file to read.")


class ReadFileTool:
    """Read a UTF-8 text file from the Workspace."""

    name = "read_file"
    requires_approval = False
    description = (
        "Read a UTF-8 text file from the workspace and return its full contents. "
        "The path must stay inside the workspace; sensitive paths are denied."
    )
    input_model: type[BaseModel] = ReadFileInput

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        params = ReadFileInput.model_validate(input)
        sandbox = context.require_sandbox()
        try:
            resolved = sandbox.resolve(params.path)
        except SandboxViolation as error:
            return ToolResult(str(error), is_error=True)
        if not resolved.is_file():
            return ToolResult(f"not a file: {params.path}", is_error=True)
        try:
            data = resolved.read_bytes()
        except OSError as error:
            return ToolResult(f"{type(error).__name__}: {error}", is_error=True)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return ToolResult(f"not a UTF-8 text file: {params.path}", is_error=True)

        # Output is bounded by the unified truncation seam in the agent loop.
        return ToolResult(text)
