from __future__ import annotations

import asyncio
import contextlib
import os
import re
import signal
from pathlib import Path

from milky_frog.core.sandbox import (
    CommandOutcome,
    CommandPresentation,
    CommandResult,
    CommandStartError,
    CommandTimeout,
)

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


async def run_local_command(
    command: str,
    *,
    workspace: Path,
    env: dict[str, str],
    timeout_seconds: float,
    presentation: CommandPresentation,
) -> CommandOutcome:
    if presentation is CommandPresentation.TERMINAL:
        env = _with_presentation_env(env)

    try:
        if os.name == "posix":
            process = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(workspace),
                env=env,
                start_new_session=True,
            )
        else:
            process = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(workspace),
                env=env,
            )
    except OSError as error:
        return CommandStartError(str(error))

    try:
        raw = await _communicate_with_timeout(process, timeout_seconds)
    except TimeoutError:
        return CommandTimeout(timeout_seconds)

    display_output = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    output = _strip_ansi(display_output)
    display = display_output if display_output != output else None
    return CommandResult(
        exit_code=process.returncode if process.returncode is not None else 0,
        output=output,
        display_output=display,
    )
