from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from pathlib import Path

from milky_frog.adapters.process import make_command_result, with_presentation_env
from milky_frog.core.cleanup import complete_cleanup
from milky_frog.core.sandbox import (
    CommandOutcome,
    CommandPresentation,
    CommandStartError,
    CommandTimeout,
)


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
        await complete_cleanup(communicate_task, propagate_cancellation=True)
        raise
    except BaseException:
        _kill_process(process)
        with contextlib.suppress(BaseException):
            await complete_cleanup(communicate_task, propagate_cancellation=False)
        raise
    return stdout if stdout is not None else b""


async def run_local_command(
    command: str,
    *,
    workspace: Path,
    env: dict[str, str],
    timeout_seconds: float,
    presentation: CommandPresentation = CommandPresentation.PLAIN,
) -> CommandOutcome:
    """Run *command* on the host, capturing stdout with stderr merged in.

    Stdin is closed so interactive prompts cannot hang the Run. On POSIX the
    child gets its own process group so a timeout kills the whole tree.
    """
    if presentation is CommandPresentation.TERMINAL:
        env = with_presentation_env(env)

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

    return make_command_result(process.returncode if process.returncode is not None else 0, raw)
