"""Container Sandbox: bind-mount the Workspace, run commands via ``docker exec``.

Path resolution and the sensitive-path deny policy are **not** reimplemented —
they are delegated to a composed ``LocalSandbox``. Because the Workspace is
bind-mounted at ``workspace_mount``, host-side file I/O (what ``read_file`` /
``write_file`` / ``grep`` already do) and container-side commands observe the
same bytes. Only command execution differs, which is exactly the seam
``Sandbox.run_command`` covers.

This is a policy boundary with real process isolation for commands, not a
security boundary against a fully-untrusted model: the bind mount means a
process in the container can still reach every file in the Workspace.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path

from milky_frog.adapters.docker.cli import (
    DOCKER_BINARY,
    DockerCli,
    DockerUnavailable,
    SubprocessDockerCli,
)
from milky_frog.adapters.local import LocalSandbox
from milky_frog.adapters.process import with_presentation_env
from milky_frog.core.sandbox import CommandOutcome, CommandPresentation, Sandbox
from milky_frog.project import ProjectConfig

_NONINTERACTIVE_ENV: dict[str, str] = {
    "CI": "true",
    "GIT_TERMINAL_PROMPT": "0",
}


def _container_name(workspace: Path) -> str:
    digest = hashlib.sha256(str(workspace).encode("utf-8")).hexdigest()[:12]
    return f"milky-frog-{digest}"


class ContainerRegistry:
    """Owns the container lifecycle for one image, keyed by Workspace.

    A container is created lazily on first use and reused for every subsequent
    command — ``docker exec`` costs tens of milliseconds where a fresh
    ``docker run`` costs hundreds. ``aclose()`` removes everything it created.
    """

    def __init__(self, *, image: str, workspace_mount: str, cli: DockerCli) -> None:
        self._image = image
        self._workspace_mount = workspace_mount
        self._cli = cli
        self._containers: dict[Path, str] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, workspace: Path) -> str:
        async with self._lock:
            existing = self._containers.get(workspace)
            if existing is not None:
                return existing
            container_id = await self._start(workspace)
            self._containers[workspace] = container_id
            return container_id

    async def _start(self, workspace: Path) -> str:
        result = await self._cli.capture(
            [
                DOCKER_BINARY,
                "run",
                "-d",
                "--name",
                _container_name(workspace),
                "-v",
                f"{workspace}:{self._workspace_mount}",
                "-w",
                self._workspace_mount,
                self._image,
                "sleep",
                "infinity",
            ]
        )
        if result.exit_code != 0:
            raise DockerUnavailable(
                f"failed to start container from image {self._image!r}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        container_id = result.stdout.strip()
        if not container_id:
            raise DockerUnavailable("docker run returned no container id")
        return container_id

    async def aclose(self) -> None:
        async with self._lock:
            containers = list(self._containers.values())
            self._containers.clear()
        for container_id in containers:
            await self._cli.capture([DOCKER_BINARY, "rm", "-f", container_id])


class DockerSandbox:
    """Sandbox adapter that executes commands inside a container."""

    def __init__(
        self,
        workspace: Path,
        config: ProjectConfig | None = None,
        *,
        workspace_mount: str,
        containers: ContainerRegistry,
        cli: DockerCli,
    ) -> None:
        self._local = LocalSandbox(workspace, config)
        self._workspace_mount = workspace_mount
        self._containers = containers
        self._cli = cli
        self.workspace = self._local.workspace
        self.config = self._local.config

    def resolve(self, relative_path: str, *, allow_sensitive: bool = False) -> Path:
        """Delegate to the composed LocalSandbox: same deny policy, host path.

        The Workspace is bind-mounted, so the host path a Tool reads and the
        container path a command sees refer to the same file.
        """
        return self._local.resolve(relative_path, allow_sensitive=allow_sensitive)

    def build_env(self) -> dict[str, str]:
        """Container env: non-interactive defaults plus opt-in host values.

        Host ``HOME`` / ``PATH`` / ``SHELL`` are deliberately *not* forwarded —
        they name host filesystem locations that mean nothing inside the image.
        ``env_allowlist_extra`` names (build vars, tokens) do travel, because
        their *value* is what matters, not a path.
        """
        env = dict(_NONINTERACTIVE_ENV)
        for name in self.config.env_allowlist_extra:
            value = os.environ.get(name)
            if value is not None:
                env[name] = value
        return env

    async def run_command(
        self,
        command: str,
        *,
        timeout_seconds: float,
        presentation: CommandPresentation = CommandPresentation.PLAIN,
    ) -> CommandOutcome:
        container_id = await self._containers.acquire(self.workspace)
        env = self.build_env()
        if presentation is CommandPresentation.TERMINAL:
            env = with_presentation_env(env)

        env_flags: list[str] = []
        for name, value in env.items():
            env_flags.extend(("-e", f"{name}={value}"))

        argv = [
            DOCKER_BINARY,
            "exec",
            "-w",
            self._workspace_mount,
            *env_flags,
            container_id,
            "sh",
            "-c",
            command,
        ]
        return await self._cli.combined(argv, timeout_seconds=timeout_seconds)


class DockerSandboxFactory:
    """``SandboxFactory`` producing ``DockerSandbox`` over a shared container registry.

    One factory per session. ``aclose()`` (wired into ``ShutdownManager``)
    removes every container it started.
    """

    def __init__(
        self,
        *,
        image: str,
        workspace_mount: str,
        cli: DockerCli | None = None,
        config: ProjectConfig | None = None,
    ) -> None:
        self._cli = cli if cli is not None else SubprocessDockerCli()
        self._workspace_mount = workspace_mount
        self._config = config
        self._containers = ContainerRegistry(
            image=image, workspace_mount=workspace_mount, cli=self._cli
        )

    def __call__(self, workspace: Path) -> Sandbox:
        return DockerSandbox(
            workspace,
            self._config,
            workspace_mount=self._workspace_mount,
            containers=self._containers,
            cli=self._cli,
        )

    async def aclose(self) -> None:
        await self._containers.aclose()
