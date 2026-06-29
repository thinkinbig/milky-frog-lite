from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from milky_frog.core.sandbox import Sandbox, SandboxViolation
from milky_frog.domain import ToolResult
from milky_frog.harness.tools.base import ToolContext
from milky_frog.harness.tools.builtins.list_dir import render_directory
from milky_frog.harness.tools.truncate import truncate_tool_output

_MAX_BYTES = 256 * 1024


class ReadFileInput(BaseModel):
    path: str = Field(description="Workspace-relative path to the file to read.")
    offset: int | None = Field(
        default=None,
        ge=1,
        description="1-based line number to start reading from. Omit to read from the start.",
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        description="Maximum number of lines to return. Omit to read to the end of the file.",
    )


class ReadFileTool:
    """Read a UTF-8 text file from the Workspace."""

    name = "read_file"
    requires_approval = False
    description = (
        "Read a UTF-8 text file from the workspace. Returns the full contents by default, or a "
        "line-range window when offset/limit are given. Prefer a narrow window over whole large "
        "files: grep to find the relevant line first, then read around it. "
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
            if resolved.is_dir():
                return self._directory_listing(params.path, resolved, sandbox)
            return ToolResult(f"not a file: {params.path}", is_error=True)
        try:
            data = resolved.read_bytes()
        except OSError as error:
            return ToolResult(f"{type(error).__name__}: {error}", is_error=True)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return ToolResult(f"not a UTF-8 text file: {params.path}", is_error=True)

        max_chars = sandbox.config.read_output_max_chars
        if params.offset is None and params.limit is None:
            return ToolResult(
                truncate_tool_output(
                    text, max_chars=max_chars, workspace=sandbox.workspace, label="read"
                )
            )

        return self._read_window(params, text, max_chars, sandbox.workspace)

    @staticmethod
    def _directory_listing(path: str, resolved: Path, sandbox: Sandbox) -> ToolResult:
        """Degrade a read of a directory to its listing instead of an error."""
        try:
            listing = render_directory(resolved)
        except OSError as error:
            return ToolResult(f"{type(error).__name__}: {error}", is_error=True)
        listing = truncate_tool_output(
            listing,
            max_chars=sandbox.config.search_output_max_chars,
            workspace=sandbox.workspace,
            label="read_dir",
        )
        return ToolResult(f"{path} is a directory, not a file. Its entries:\n{listing}")

    @staticmethod
    def _read_window(
        params: ReadFileInput, text: str, max_chars: int, workspace: Path
    ) -> ToolResult:
        """Return only the requested line window, with a header when partial."""
        lines = text.splitlines(keepends=True)
        total = len(lines)
        start = (params.offset or 1) - 1
        if total and start >= total:
            return ToolResult(
                f"offset {params.offset} is past the end of the file "
                f"({total} lines): {params.path}",
                is_error=True,
            )
        end = total if params.limit is None else min(total, start + params.limit)
        window = truncate_tool_output(
            "".join(lines[start:end]), max_chars=max_chars, workspace=workspace, label="read"
        )
        if start > 0 or end < total:
            window = f"[lines {start + 1}-{end} of {total}]\n{window}"
        return ToolResult(window)
