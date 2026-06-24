"""Sandbox protocol — core seam for Workspace execution policy."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from milky_frog.project import ProjectConfig


class SandboxViolation(PermissionError):
    """Raised when a path resolution violates the Sandbox policy."""


class Sandbox(Protocol):
    """Policy boundary for Workspace execution."""

    workspace: Path
    config: ProjectConfig

    def resolve(self, relative_path: str, *, allow_sensitive: bool = False) -> Path: ...

    def build_env(self) -> dict[str, str]: ...


class SandboxFactory(Protocol):
    """Create a Sandbox for a given Workspace."""

    def __call__(self, workspace: Path) -> Sandbox: ...
