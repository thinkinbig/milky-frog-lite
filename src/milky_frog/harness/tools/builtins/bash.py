from __future__ import annotations

import asyncio
import os
import pty

from pydantic import BaseModel, Field

from milky_frog.domain import ToolResult
from milky_frog.harness.tools.base import ToolContext

_BASH_TIMEOUT_SECONDS = 30.0
_MAX_OUTPUT_BYTES = 128 * 1024
# Grace period to drain PTY output after the process exits.
_PTY_DRAIN_SECONDS = 2.0


class BashInput(BaseModel):
    command: str = Field(
        description="Shell command to run in the workspace directory. "
        "Use for file operations (grep, find, ls, cat, head, tail, sort, wc, etc.), "
        "build commands, testing, and general shell operations.",
    )


async def _read_pty(
    loop: asyncio.AbstractEventLoop, master_fd: int, max_bytes: int
) -> bytes:
    """Read from a PTY master fd until EIO (slave closed).

    Accumulates up to ``max_bytes`` bytes, then drains (reads and discards)
    so the child never blocks on a full PTY buffer.  Returns when the slave
    is fully closed (child exited).

    Driven by loop.add_reader so we never block the event loop.  CancelledError
    is propagated cleanly — the reader callback is removed before re-raising.
    """
    future: asyncio.Future[bytes] = loop.create_future()
    chunks: list[bytes] = []
    total = 0

    def _done() -> None:
        loop.remove_reader(master_fd)
        if not future.done():
            future.set_result(b"".join(chunks))

    def _on_readable() -> None:
        nonlocal total
        try:
            data = os.read(master_fd, 4096)
        except OSError:
            # EIO: every slave fd was closed (child exited).
            _done()
            return
        if not data:
            _done()
            return
        if total < max_bytes:
            chunks.append(data)
            total += len(data)
        # Beyond max_bytes: keep reading so the child doesn't block on a full PTY buffer.

    loop.add_reader(master_fd, _on_readable)
    try:
        return await future
    except asyncio.CancelledError:
        loop.remove_reader(master_fd)
        raise


class BashTool:
    """Run a shell command inside the Workspace directory and capture output.

    The command runs inside a PTY so programs that check isatty() (git, grep,
    ls …) emit colour and formatting naturally.  Output is truncated at 128 KB;
    timeout is 30 seconds.
    """

    name = "bash"
    requires_approval = True
    description = (
        "Run a shell command in the workspace and capture its stdout and stderr. "
        "The command is executed with a clean environment (only HOME, PATH, SHELL, "
        "TERM, LANG, LC_ALL, TMPDIR are passed through). "
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
        path = env.get("PATH", "")
        if "/usr/local/bin" not in path:
            env["PATH"] = f"/usr/local/bin:{path}" if path else "/usr/local/bin"

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

            read_task = asyncio.create_task(_read_pty(loop, master_fd, _MAX_OUTPUT_BYTES + 1))

            try:
                await asyncio.wait_for(process.wait(), timeout=_BASH_TIMEOUT_SECONDS)
            except TimeoutError:
                process.kill()
                await process.wait()
                read_task.cancel()
                try:
                    await read_task
                except asyncio.CancelledError:
                    pass
                read_task = None
                return ToolResult(
                    f"command timed out after {_BASH_TIMEOUT_SECONDS}s", is_error=True
                )

            # Process exited — drain any output still buffered in the PTY.
            try:
                raw = await asyncio.wait_for(read_task, timeout=_PTY_DRAIN_SECONDS)
            except TimeoutError:
                read_task.cancel()
                try:
                    await read_task
                except asyncio.CancelledError:
                    pass
                raw = b""
            read_task = None

        finally:
            if read_task is not None:
                read_task.cancel()
                try:
                    await read_task
                except (asyncio.CancelledError, Exception):
                    pass
            os.close(master_fd)
            if slave_fd >= 0:
                os.close(slave_fd)

        # PTY uses \r\n line endings; normalise to \n.
        text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")

        if process.returncode != 0:
            stripped = text.strip() or "(no output)"
            return ToolResult(f"exit code {process.returncode}:\n{stripped}", is_error=True)

        if len(raw) > _MAX_OUTPUT_BYTES:
            truncated = text[:_MAX_OUTPUT_BYTES]
            msg = (
                f"output truncated ({len(raw)} bytes; "
                f"showing first {_MAX_OUTPUT_BYTES}):\n{truncated}"
            )
            return ToolResult(msg)

        result = text.rstrip("\n")
        return ToolResult(result if result else "(no output)")
