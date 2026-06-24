from __future__ import annotations

import asyncio
import contextlib
import os
import pty

from pydantic import BaseModel, Field

from milky_frog.domain import ToolResult
from milky_frog.harness.tools.base import ToolContext
from milky_frog.harness.tools.truncate import truncate_tool_output
from milky_frog.project import DEFAULT_BASH_TIMEOUT_SECONDS, load_project_config

_MAX_OUTPUT_BYTES = 128 * 1024
# Grace period to drain PTY output after the process exits.
_PTY_DRAIN_SECONDS = 2.0
# Host env vars passed through to a spawned local command (never secrets).
_COMMAND_ENV_ALLOWLIST = ("HOME", "LANG", "LC_ALL", "PATH", "SHELL", "TERM", "TMPDIR")


def local_command_environment() -> dict[str, str]:
    """Build the final environment for a spawned local command.

    Passes through only an allowlist of host env vars and ensures
    ``/usr/local/bin`` is on ``PATH``.  This is execution-mechanism config, not
    a Sandbox/Workspace policy — a future Docker runner would supply env
    differently (e.g. ``docker exec -e``), so it lives with command execution
    rather than on the Sandbox.
    """
    env = {name: os.environ[name] for name in _COMMAND_ENV_ALLOWLIST if name in os.environ}
    path = env.get("PATH", "")
    if "/usr/local/bin" not in path:
        env["PATH"] = f"/usr/local/bin:{path}" if path else "/usr/local/bin"
    return env


class BashInput(BaseModel):
    command: str = Field(
        description="Shell command to run in the workspace directory. "
        "Prefer narrow, scoped commands: git diff --stat or git diff <file> | tail N "
        "instead of bare git diff; grep -c or grep <path> instead of repo-wide grep; "
        "find with -name instead of listing huge directories. "
        "Output is truncated at 128 KB with head/tail and a spill path when larger.",
    )


async def _read_pty(loop: asyncio.AbstractEventLoop, master_fd: int) -> bytes:
    """Read from a PTY master fd until EIO (slave closed).

    Accumulates output and returns when the slave
    is fully closed (child exited).

    Driven by loop.add_reader so we never block the event loop.  CancelledError
    is propagated cleanly — the reader callback is removed before re-raising.
    """
    future: asyncio.Future[bytes] = loop.create_future()
    chunks: list[bytes] = []

    def _done() -> None:
        loop.remove_reader(master_fd)
        if not future.done():
            future.set_result(b"".join(chunks))

    def _on_readable() -> None:
        try:
            data = os.read(master_fd, 4096)
        except OSError:
            # EIO: every slave fd was closed (child exited).
            _done()
            return
        if not data:
            _done()
            return
        # Cap memory at 10MB to avoid OOM
        if sum(len(c) for c in chunks) < 10 * 1024 * 1024:
            chunks.append(data)

    loop.add_reader(master_fd, _on_readable)
    try:
        return await future
    except asyncio.CancelledError:
        loop.remove_reader(master_fd)
        raise


class BashTool:
    """Run a shell command inside the Workspace directory and capture output.

    The command runs inside a PTY so programs that check isatty() (git, grep,
    ls …) emit colour and formatting naturally.  Output is truncated at 128 KB.
    Timeout defaults to five minutes and is configurable via
    ``bash_timeout_seconds`` in ``.milky-frog/config.toml``.
    """

    name = "bash"
    requires_approval = True
    description = (
        "Run a shell command in the workspace and capture its stdout and stderr. "
        "Prefer scoped commands over repo-wide dumps — e.g. git diff --stat, "
        "git diff <file> | tail, grep -rl <pattern> <dir>, find -name. "
        "Large output is truncated at 128 KB (head/tail plus a spill file path). "
        "The command runs with a clean environment (HOME, PATH, SHELL, TERM, LANG, "
        "LC_ALL, TMPDIR only). "
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
        env = local_command_environment()
        timeout_seconds = float(load_project_config(sandbox.workspace).bash_timeout_seconds)

        loop = asyncio.get_running_loop()
        master_fd, slave_fd = pty.openpty()
        read_task: asyncio.Task[bytes] | None = None
        try:
            try:
                process = await asyncio.create_subprocess_shell(
                    command,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    cwd=str(sandbox.workspace),
                    env=env,
                )
            except OSError as error:
                return ToolResult(f"failed to run command: {error}", is_error=True)

            # Parent closes slave: when the child exits, EIO on master signals EOF.
            os.close(slave_fd)
            slave_fd = -1

            read_task = asyncio.create_task(_read_pty(loop, master_fd))

            try:
                await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
            except TimeoutError:
                process.kill()
                await process.wait()
                read_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await read_task
                read_task = None
                return ToolResult(f"command timed out after {timeout_seconds:g}s", is_error=True)

            # Process exited — drain any output still buffered in the PTY.
            try:
                raw = await asyncio.wait_for(read_task, timeout=_PTY_DRAIN_SECONDS)
            except TimeoutError:
                read_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await read_task
                raw = b""
            read_task = None

        finally:
            if read_task is not None:
                read_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await read_task
            os.close(master_fd)
            if slave_fd >= 0:
                os.close(slave_fd)

        # PTY uses \r\n line endings; normalise to \n.
        text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")

        if process.returncode != 0:
            text = truncate_tool_output(text, max_chars=128000, tool_name="bash")
            stripped = text.strip() or "(no output)"
            return ToolResult(f"exit code {process.returncode}:\n{stripped}", is_error=True)

        text = truncate_tool_output(text, max_chars=128000, tool_name="bash")
        result = text.rstrip("\n")
        return ToolResult(result if result else "(no output)")
