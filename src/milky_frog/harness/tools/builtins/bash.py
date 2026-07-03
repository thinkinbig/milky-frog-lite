from __future__ import annotations

from pydantic import BaseModel, Field

from milky_frog.core.sandbox import (
    CommandPresentation,
    CommandResult,
    CommandStartError,
    CommandTimeout,
)
from milky_frog.domain import ToolResult
from milky_frog.harness.tools.base import ToolContext
from milky_frog.harness.tools.truncate import truncate_tool_output
from milky_frog.project import DEFAULT_BASH_OUTPUT_MAX_CHARS, DEFAULT_BASH_TIMEOUT_SECONDS


class BashInput(BaseModel):
    command: str = Field(description="Shell command to run in the workspace directory.")


class BashTool:
    """Run a shell command inside the Workspace directory and capture output.

    The command runs non-interactively with stdout/stderr captured through a
    subprocess pipe.  Stdin is closed and the environment disables pagers and
    interactive prompts so git log and similar commands cannot hang waiting for
    a human.  Oversized output is passed to ``truncate_tool_output``
    (``bash_output_max_chars`` in ``.milky-frog/config.toml``). Timeout is
    configurable via ``bash_timeout_seconds`` in the same file.
    """

    name = "bash"
    requires_approval = True
    description = (
        "Run a shell command in the workspace and capture its stdout and stderr. "
        "Large output is truncated via head/tail with the full text spilled to disk "
        f"(default inline cap {DEFAULT_BASH_OUTPUT_MAX_CHARS} chars; "
        "override bash_output_max_chars in .milky-frog/config.toml). "
        "Commands run non-interactively (no pagers or terminal prompts; stdin closed). "
        "Host env is limited to HOME, PATH, SHELL, TERM, LANG, LC_ALL, TMPDIR. "
        f"Default timeout is {DEFAULT_BASH_TIMEOUT_SECONDS} seconds; "
        "override with bash_timeout_seconds in .milky-frog/config.toml."
    )
    input_model: type[BaseModel] = BashInput

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        params = BashInput.model_validate(input)
        command = params.command.strip()
        if not command:
            return ToolResult("empty command", is_error=True)

        sandbox = context.require_sandbox()
        timeout_seconds = float(sandbox.config.bash_timeout_seconds)
        outcome = await sandbox.run_command(
            command,
            timeout_seconds=timeout_seconds,
            presentation=CommandPresentation.TERMINAL,
        )

        if isinstance(outcome, CommandStartError):
            return ToolResult(f"failed to run command: {outcome.message}", is_error=True)
        if isinstance(outcome, CommandTimeout):
            return ToolResult(f"command timed out after {outcome.seconds:g}s", is_error=True)
        if not isinstance(outcome, CommandResult):
            return ToolResult("unknown command outcome", is_error=True)

        max_chars = sandbox.config.bash_output_max_chars
        text = outcome.output
        display_content = outcome.display_output
        if len(text) > max_chars:
            display_content = None

        if outcome.exit_code != 0:
            text = truncate_tool_output(
                text,
                max_chars=max_chars,
                workspace=sandbox.workspace,
                label="bash",
                counter=context.token_counter,
            )
            stripped = text.strip() or "(no output)"
            display_result = (
                f"exit code {outcome.exit_code}:\n{display_content.strip() or '(no output)'}"
                if display_content is not None
                else None
            )
            return ToolResult(
                f"exit code {outcome.exit_code}:\n{stripped}",
                is_error=True,
                display_content=display_result,
            )

        text = truncate_tool_output(
            text,
            max_chars=max_chars,
            workspace=sandbox.workspace,
            label="bash",
            counter=context.token_counter,
        )
        result = text.rstrip("\n")
        display_result = display_content.rstrip("\n") if display_content is not None else None
        return ToolResult(result if result else "(no output)", display_content=display_result)
