"""The one place Milky Frog shells out to the ``docker`` binary.

Isolating it behind a Protocol keeps ``DockerSandbox`` unit-testable without a
running daemon: the tests inject a stub that records argv.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from milky_frog.adapters.process import make_command_result
from milky_frog.core.sandbox import CommandOutcome, CommandStartError, CommandTimeout

DOCKER_BINARY = "docker"


class DockerUnavailable(RuntimeError):
    """Raised when the ``docker`` CLI is missing or the daemon is unreachable."""


@dataclass(frozen=True, slots=True)
class DockerCliResult:
    """A finished ``docker`` invocation with stdout and stderr kept apart."""

    exit_code: int
    stdout: str
    stderr: str


class DockerCli(Protocol):
    """Seam over the ``docker`` binary."""

    async def capture(self, argv: Sequence[str]) -> DockerCliResult:
        """Run a lifecycle command (``run``/``rm``/``version``), no timeout."""
        ...

    async def combined(self, argv: Sequence[str], *, timeout_seconds: float) -> CommandOutcome:
        """Run a command with stderr merged into stdout, under a timeout."""
        ...


class SubprocessDockerCli:
    """Default ``DockerCli``: ``asyncio.create_subprocess_exec`` on the host."""

    async def capture(self, argv: Sequence[str]) -> DockerCliResult:
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            raise DockerUnavailable(f"cannot run {DOCKER_BINARY}: {error}") from error

        stdout, stderr = await process.communicate()
        return DockerCliResult(
            exit_code=process.returncode if process.returncode is not None else 0,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )

    async def combined(self, argv: Sequence[str], *, timeout_seconds: float) -> CommandOutcome:
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as error:
            return CommandStartError(str(error))

        communicate = asyncio.create_task(process.communicate())
        try:
            stdout, _ = await asyncio.wait_for(asyncio.shield(communicate), timeout=timeout_seconds)
        except TimeoutError:
            # Kills the host-side `docker exec` client. The in-container
            # process may survive until the container itself is removed.
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await communicate
            return CommandTimeout(timeout_seconds)
        except BaseException:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            with contextlib.suppress(BaseException):
                await communicate
            raise

        return make_command_result(
            process.returncode if process.returncode is not None else 0,
            stdout if stdout is not None else b"",
        )


async def docker_is_available(cli: DockerCli | None = None) -> bool:
    """Whether ``docker version`` succeeds. Used by ``doctor`` and integration tests."""
    runner = cli if cli is not None else SubprocessDockerCli()
    try:
        result = await runner.capture([DOCKER_BINARY, "version", "--format", "{{.Server.Version}}"])
    except DockerUnavailable:
        return False
    return result.exit_code == 0
