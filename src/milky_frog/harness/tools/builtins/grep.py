from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from milky_frog.domain import ToolResult
from milky_frog.harness.tools.base import ToolContext

_RG_TIMEOUT_SECONDS = 15.0
_MAX_OUTPUT_BYTES = 128 * 1024


class GrepInput(BaseModel):
    pattern: str = Field(
        description="Regex pattern to search for, e.g. 'def _execute' or 'class Tool'.",
    )
    path: str = Field(
        default=".",
        description=(
            "Workspace-relative directory or file to search in. Defaults to the workspace root."
        ),
    )


class GrepTool:
    """Search file contents with ripgrep."""

    name = "grep"
    requires_approval = False
    description = (
        "Search file contents in the workspace with a regex pattern using ripgrep (rg). "
        "Returns matching lines with file paths and line numbers. "
        "Use this before reading files — search for a class/function name or keyword first, "
        "then read only the files that match."
    )
    input_model: type[BaseModel] = GrepInput

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        params = GrepInput.model_validate(input)
        pattern = params.pattern.strip()
        if not pattern:
            return ToolResult("empty grep pattern", is_error=True)

        sandbox = context.require_sandbox()
        search_dir = str(sandbox.workspace / params.path)
        env = sandbox.command_environment()

        args = ["rg", "--no-heading", "-n", "--color", "never", "-M", "300", pattern, search_dir]
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError:
            return ToolResult(
                "ripgrep (rg) is required but not found on PATH. Install: brew install ripgrep",
                is_error=True,
            )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=_RG_TIMEOUT_SECONDS
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return ToolResult(f"rg timed out after {_RG_TIMEOUT_SECONDS}s", is_error=True)

        rc = process.returncode or 0

        # rg exit codes: 0 = matches found, 1 = no matches, 2 = error
        if rc >= 2:
            error_text = stderr_bytes.decode("utf-8", errors="replace").strip() or "(no stderr)"
            return ToolResult(f"rg error (exit {rc}): {error_text}", is_error=True)

        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        if not stdout_text.strip():
            return ToolResult("(no matches)")

        if len(stdout_text) > _MAX_OUTPUT_BYTES:
            truncated = stdout_text[:_MAX_OUTPUT_BYTES]
            msg = (
                f"output truncated ({len(stdout_text)} bytes; "
                f"showing first {_MAX_OUTPUT_BYTES}):\n{truncated}"
            )
            return ToolResult(msg)
        return ToolResult(stdout_text)
