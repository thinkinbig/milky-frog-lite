from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from milky_frog.domain import ToolResult
from milky_frog.harness.tools.base import ToolContext

_BASH_TIMEOUT_SECONDS = 30.0
_MAX_OUTPUT_BYTES = 128 * 1024


class BashInput(BaseModel):
    command: str = Field(
        description="Shell command to run in the workspace directory. "
        "Use for file operations (grep, find, ls, cat, head, tail, sort, wc, etc.), "
        "build commands, testing, and general shell operations.",
    )


class BashTool:
    """Run a shell command inside the Workspace directory and capture output.

    The command is executed with a clean, allow-listed environment and a
    30-second timeout.  Long output is truncated at 128 KB.
    """

    name = "bash"
    requires_approval = True
    description = (
        "Run a shell command in the workspace and capture its stdout and stderr. "
        "The command is executed with a clean environment (only HOME, PATH, SHELL, "
        "TERM, LANG, LC_ALL, TMPDIR are pass through). "
        "Output is truncated at 128 KB; timeout is 30 seconds."
    )
    input_model: type[BaseModel] = BashInput

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        params = BashInput.model_validate(input)
        command = params.command.strip()
        if not command:
            return ToolResult("empty command", is_error=True)

        sandbox = context.require_sandbox()
        env = sandbox.command_environment()
        # Ensure /usr/local/bin is on PATH for tools like rg
        path = env.get("PATH", "")
        if "/usr/local/bin" not in path:
            env["PATH"] = f"/usr/local/bin:{path}" if path else "/usr/local/bin"

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(sandbox.workspace),
                env=env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=_BASH_TIMEOUT_SECONDS
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                msg = f"command timed out after {_BASH_TIMEOUT_SECONDS}s"
                return ToolResult(msg, is_error=True)
        except OSError as error:
            return ToolResult(f"failed to run command: {error}", is_error=True)

        if process.returncode != 0:
            error_text = stderr.decode("utf-8", errors="replace").strip() or "(no stderr)"
            stdout_text = stdout.decode("utf-8", errors="replace").strip()
            combined = error_text
            if stdout_text:
                combined = f"{stdout_text}\n{error_text}"
            return ToolResult(f"exit code {process.returncode}:\n{combined}", is_error=True)

        stdout_text = stdout.decode("utf-8", errors="replace")
        if len(stdout) > _MAX_OUTPUT_BYTES:
            truncated = stdout_text[:_MAX_OUTPUT_BYTES]
            msg = (
                f"output truncated ({len(stdout)} bytes; "
                f"showing first {_MAX_OUTPUT_BYTES}):\n{truncated}"
            )
            return ToolResult(msg)
        result = stdout_text.rstrip("\n")
        return ToolResult(result if result else "(no output)")
