from __future__ import annotations

import asyncio
import shlex

from pydantic import BaseModel, Field

from milky_frog.domain import ToolResult
from milky_frog.harness.tools.base import ToolContext

_GIT_TIMEOUT_SECONDS = 30.0
_MAX_OUTPUT_BYTES = 128 * 1024


class GitInput(BaseModel):
    command: str = Field(
        description="Git subcommand with arguments, e.g. 'status', 'diff', 'log --oneline -5'.",
    )


class GitTool:
    """Run a git command inside the Workspace directory."""

    name = "git"
    description = (
        "Run a git subcommand in the workspace repository and return its stdout. "
        "Allowed subcommands: status, diff, diff --staged, log, branch, tag, show, "
        "add, reset, commit, stash, blame, rev-parse, remote, config --list. "
        "Commands that modify the working tree or history (e.g. commit, add, reset) "
        "are visible to the model but require user approval through the Handler layer. "
        "The command is executed with a clean, allow-listed environment."
    )
    input_model: type[BaseModel] = GitInput

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        params = GitInput.model_validate(input)
        command_str = params.command.strip()
        if not command_str:
            return ToolResult("empty git command", is_error=True)
        sandbox = context.require_sandbox()
        try:
            tokens = shlex.split(command_str)
        except ValueError as error:
            return ToolResult(f"invalid git command: {error}", is_error=True)
        if not tokens or tokens[0] != "git":
            tokens = ["git", *tokens]
        else:
            # User already included "git"; just use as-is
            pass
        env = sandbox.command_environment()
        try:
            process = await asyncio.create_subprocess_exec(
                *tokens,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(sandbox.workspace),
                env=env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=_GIT_TIMEOUT_SECONDS
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                msg = f"git command timed out after {_GIT_TIMEOUT_SECONDS}s"
                return ToolResult(msg, is_error=True)
        except OSError as error:
            return ToolResult(f"failed to run git: {error}", is_error=True)

        if process.returncode != 0:
            error_text = stderr.decode("utf-8", errors="replace").strip() or "(no stderr)"
            return ToolResult(f"git exited {process.returncode}: {error_text}", is_error=True)

        stdout_text = stdout.decode("utf-8", errors="replace")
        if len(stdout) > _MAX_OUTPUT_BYTES:
            truncated = stdout_text[:_MAX_OUTPUT_BYTES]
            msg = (
                f"output truncated ({len(stdout)} bytes; "
                f"showing first {_MAX_OUTPUT_BYTES}):\n{truncated}"
            )
            return ToolResult(msg)
        return ToolResult(stdout_text if stdout_text else "(no output)")
