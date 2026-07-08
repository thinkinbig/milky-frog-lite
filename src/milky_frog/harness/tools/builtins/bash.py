from __future__ import annotations

import asyncio
import contextlib
import os
import re
import signal

from pydantic import BaseModel, Field

from milky_frog.domain import ToolResult
from milky_frog.harness.tools.base import ToolContext
from milky_frog.harness.tools.truncate import truncate_tool_output
from milky_frog.project import DEFAULT_BASH_OUTPUT_MAX_CHARS, DEFAULT_BASH_TIMEOUT_SECONDS

_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\a]*(?:\a|\x1b\\))")

_PRESENTATION_ENV: dict[str, str] = {
    "COLORTERM": "truecolor",
    "CLICOLOR_FORCE": "1",
    "FORCE_COLOR": "1",
}

_GIT_COLOR_ENV: dict[str, str] = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "color.ui",
    "GIT_CONFIG_VALUE_0": "always",
}


class BashInput(BaseModel):
    command: str = Field(description="Shell command to run in the workspace directory.")


def _with_presentation_env(env: dict[str, str]) -> dict[str, str]:
    enriched = {**env, **_PRESENTATION_ENV}
    enriched.setdefault("TERM", "xterm-256color")
    if "GIT_CONFIG_COUNT" not in enriched:
        enriched.update(_GIT_COLOR_ENV)
    return enriched


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _kill_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "posix":
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        return
    process.kill()


async def _communicate_with_timeout(
    process: asyncio.subprocess.Process, timeout_seconds: float
) -> bytes:
    communicate_task = asyncio.create_task(process.communicate())
    try:
        stdout, _stderr = await asyncio.wait_for(
            asyncio.shield(communicate_task), timeout=timeout_seconds
        )
    except TimeoutError:
        _kill_process(process)
        await communicate_task
        raise
    except BaseException:
        _kill_process(process)
        raise
    return stdout if stdout is not None else b""


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
        "The environment is a small allowlist, not your shell's: locally HOME, PATH, "
        "SHELL, TERM, LANG, LC_ALL, TMPDIR; under the container Sandbox no host "
        "variables are forwarded at all. "
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
        env = _with_presentation_env(sandbox.build_env())
        timeout_seconds = float(sandbox.config.bash_timeout_seconds)

        try:
            if os.name == "posix":
                process = await asyncio.create_subprocess_shell(
                    command,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=str(sandbox.workspace),
                    env=env,
                    start_new_session=True,
                )
            else:
                process = await asyncio.create_subprocess_shell(
                    command,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=str(sandbox.workspace),
                    env=env,
                )
        except OSError as error:
            return ToolResult(f"failed to run command: {error}", is_error=True)

        try:
            raw = await _communicate_with_timeout(process, timeout_seconds)
        except TimeoutError:
            return ToolResult(f"command timed out after {timeout_seconds:g}s", is_error=True)

        # Normalize terminal-style carriage returns and platform line endings.
        display_text = (
            raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        )
        text = _strip_ansi(display_text)
        display_content = display_text if display_text != text else None

        max_chars = sandbox.config.bash_output_max_chars
        if len(text) > max_chars:
            display_content = None

        if process.returncode != 0:
            text = truncate_tool_output(
                text,
                max_chars=max_chars,
                workspace=sandbox.workspace,
                label="bash",
                counter=context.token_counter,
            )
            stripped = text.strip() or "(no output)"
            display_result = (
                f"exit code {process.returncode}:\n{display_content.strip() or '(no output)'}"
                if display_content is not None
                else None
            )
            return ToolResult(
                f"exit code {process.returncode}:\n{stripped}",
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
