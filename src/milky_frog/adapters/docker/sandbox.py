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
import contextlib
import hashlib
import logging
import os
import uuid
from collections.abc import Sequence
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

logger = logging.getLogger(__name__)

_NONINTERACTIVE_ENV: dict[str, str] = {
    "CI": "true",
    "GIT_TERMINAL_PROMPT": "0",
}

WORKSPACE_LABEL = "milky-frog.workspace"


def _workspace_digest(workspace: Path) -> str:
    return hashlib.sha256(str(workspace).encode("utf-8")).hexdigest()[:12]


def _container_name(workspace: Path) -> str:
    """Build a per-start container name: stable prefix, unique suffix.

    The name is *not* fully deterministic from the Workspace alone. If it
    were, a container orphaned by a cancelled ``docker run`` (the daemon
    created it before the client was interrupted, e.g. Ctrl-C mid image-pull)
    would sit there under that exact name with nothing tracking or removing
    it. The next ``acquire()`` for the same Workspace would then run
    ``docker run --name <same name>``, collide with its own orphaned
    predecessor, fail with "name already in use", and raise
    ``DockerUnavailable`` forever — wedging the Workspace until a human runs
    ``docker rm`` by hand. Appending a fresh random suffix on every start
    means two starts never collide, so an orphan can never block its
    successor.

    The ``milky-frog-`` prefix is kept stable so operators can still find
    every container this project created with ``docker ps --filter
    name=milky-frog``; the ``milky-frog.workspace`` label (see
    ``WORKSPACE_LABEL``) is what makes containers discoverable *by Workspace*
    now that the name itself no longer encodes one deterministically.
    """
    unique = uuid.uuid4().hex[:8]
    return f"milky-frog-{_workspace_digest(workspace)}-{unique}"


class ContainerRegistry:
    """Owns the container lifecycle for one image, keyed by Workspace.

    A container is created lazily on first use and reused for every subsequent
    command — ``docker exec`` costs tens of milliseconds where a fresh
    ``docker run`` costs hundreds. ``aclose()`` removes everything it created.
    """

    def __init__(
        self,
        *,
        image: str,
        workspace_mount: str,
        cli: DockerCli,
        mask_paths: Sequence[str] = (),
    ) -> None:
        self._image = image
        self._workspace_mount = workspace_mount
        self._cli = cli
        self._mask_paths = tuple(mask_paths)
        self._containers: dict[Path, str] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    def _mask_flags(self) -> list[str]:
        """Anonymous volumes that shadow host-built directories in the mount.

        A `-v` with only a container path gives that path a fresh empty volume,
        overlaying whatever the bind mount put there. The host's copy is not
        touched. These volumes are anonymous, so `docker rm` must be given `-v`
        or they accumulate on the host forever.
        """
        flags: list[str] = []
        for relative in self._mask_paths:
            flags.extend(("-v", f"{self._workspace_mount}/{relative}"))
        return flags

    async def acquire(self, workspace: Path) -> str:
        async with self._lock:
            if self._closed:
                raise DockerUnavailable("container registry is closed")
            existing = self._containers.get(workspace)
            if existing is not None:
                return existing
            container_id = await self._start(workspace)
            self._containers[workspace] = container_id
            return container_id

    async def _start(self, workspace: Path) -> str:
        container_name = _container_name(workspace)
        workspace_digest = _workspace_digest(workspace)
        try:
            result = await self._cli.capture(
                [
                    DOCKER_BINARY,
                    "run",
                    "-d",
                    "--name",
                    container_name,
                    "--label",
                    f"{WORKSPACE_LABEL}={workspace_digest}",
                    "-v",
                    f"{workspace}:{self._workspace_mount}",
                    *self._mask_flags(),
                    "-w",
                    self._workspace_mount,
                    self._image,
                    "sleep",
                    "infinity",
                ]
            )
        except BaseException:
            # Cancelled (Ctrl-C) or the client died mid-run: the daemon may have
            # created the container anyway, and we never learned its id. The name
            # is unique to this attempt, so removing by name cannot touch another
            # process's container — and without this the container is orphaned
            # with nothing left holding a reference to it.
            with contextlib.suppress(BaseException):
                await self._cli.capture([DOCKER_BINARY, "rm", "-f", "-v", container_name])
            raise
        if result.exit_code != 0:
            # The name is unique to this start attempt, so it cannot belong to
            # a concurrently running milky-frog process — safe to remove.
            with contextlib.suppress(Exception):
                await self._cli.capture([DOCKER_BINARY, "rm", "-f", "-v", container_name])
            raise DockerUnavailable(
                f"failed to start container from image {self._image!r}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        container_id = lines[-1] if lines else ""
        if not container_id:
            # The container is already running (exit code 0) even though we
            # cannot use its id — best-effort cleanup by name so the next
            # acquire() for this Workspace doesn't hit "name already in use".
            with contextlib.suppress(Exception):
                await self._cli.capture([DOCKER_BINARY, "rm", "-f", "-v", container_name])
            raise DockerUnavailable("docker run returned no container id")
        return container_id

    async def aclose(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            containers = list(self._containers.values())
            self._containers.clear()
        for container_id in containers:
            # One failing removal must not strand the containers after it —
            # `_containers` is already cleared, so nothing else will retry them.
            # Suppressed, but never silent: a surviving container is exactly the
            # thing an operator needs told, and `docker ps` will not explain why.
            try:
                await self._cli.capture([DOCKER_BINARY, "rm", "-f", "-v", container_id])
            except Exception:
                logger.exception(
                    "failed to remove container %s; it may be left running", container_id
                )


class DockerSandbox:
    """Sandbox adapter that executes commands inside a container.

    Known limitations, accepted for this MVP rather than oversights:

    A timeout only kills the host-side ``docker exec`` client; the
    in-container process it started may keep running until the container
    itself is removed. A dead container is not detected or recreated either:
    once a container id is cached for a Workspace it is reused for the life
    of the Run, so if the container dies underneath us (an OOM kill, a
    daemon restart) every subsequent ``run_command()`` keeps reusing the
    dead id and ``docker exec`` fails; recovering requires restarting the
    Run. Finally, ``docker exec``'s own failure codes are not distinguished
    from the invoked command's: ``docker exec`` returns 126/127 for its own
    failures (which can collide with the command's real exit codes), and its
    error text is merged into the command output by ``stderr=STDOUT``, so a
    container-level failure is indistinguishable from the command itself
    failing.
    """

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
        mask_paths: Sequence[str] = (),
    ) -> None:
        self._cli = cli if cli is not None else SubprocessDockerCli()
        self._workspace_mount = workspace_mount
        self._config = config
        self._containers = ContainerRegistry(
            image=image,
            workspace_mount=workspace_mount,
            cli=self._cli,
            mask_paths=mask_paths,
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
