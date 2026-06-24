from __future__ import annotations

from pydantic import BaseModel, Field

from milky_frog.domain import ToolResult
from milky_frog.harness.sandbox import SandboxViolation
from milky_frog.harness.tools.base import ToolContext


class WriteFileInput(BaseModel):
    path: str = Field(description="Workspace-relative path to create or overwrite.")
    content: str = Field(description="UTF-8 text to write as the file's full contents.")


class WriteFileTool:
    """Create or overwrite a UTF-8 text file in the Workspace."""

    name = "write_file"
    requires_approval = True
    description = (
        "Create or overwrite a UTF-8 text file in the workspace with the given content, "
        "creating parent directories as needed. The path must stay inside the workspace; "
        "sensitive paths are denied."
    )
    input_model: type[BaseModel] = WriteFileInput

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        params = WriteFileInput.model_validate(input)
        sandbox = context.require_sandbox()
        try:
            resolved = sandbox.resolve(params.path)
        except SandboxViolation as error:
            return ToolResult(str(error), is_error=True)
        if resolved.is_dir():
            return ToolResult(f"path is a directory: {params.path}", is_error=True)
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            data = params.content.encode("utf-8")
            resolved.write_bytes(data)
        except OSError as error:
            return ToolResult(f"{type(error).__name__}: {error}", is_error=True)
        return ToolResult(f"wrote {len(data)} bytes to {params.path}")
