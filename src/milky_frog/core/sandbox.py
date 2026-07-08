"""Sandbox protocol — core seam for Workspace execution policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from milky_frog.project import ProjectConfig


class SandboxViolation(PermissionError):
    """Raised when a path resolution violates the Sandbox policy."""


class CommandPresentation(StrEnum):
    """How much terminal presentation a command runner should request."""

    PLAIN = "plain"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Completed command output captured under Sandbox policy."""

    exit_code: int
    output: str
    display_output: str | None = None


@dataclass(frozen=True, slots=True)
class CommandTimeout:
    """Command exceeded its configured timeout."""

    seconds: float


@dataclass(frozen=True, slots=True)
class CommandStartError:
    """Command could not be started by the Sandbox adapter."""

    message: str


CommandOutcome = CommandResult | CommandTimeout | CommandStartError


class Sandbox(Protocol):
    """Policy boundary for Workspace execution."""

    workspace: Path
    config: ProjectConfig

    def resolve(self, relative_path: str, *, allow_sensitive: bool = False) -> Path: ...

    def build_env(self) -> dict[str, str]: ...

    async def run_command(
        self,
        command: str,
        *,
        timeout_seconds: float,
        presentation: CommandPresentation = CommandPresentation.PLAIN,
    ) -> CommandOutcome: ...


class SandboxFactory(Protocol):
    """Create a Sandbox for a given Workspace."""

    def __call__(self, workspace: Path) -> Sandbox: ...
