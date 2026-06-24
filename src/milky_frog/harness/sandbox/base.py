from __future__ import annotations

from pathlib import Path
from typing import Protocol


class Sandbox(Protocol):
    """Policy boundary for structured Workspace operations."""

    workspace: Path

    def resolve(self, relative_path: str, *, allow_sensitive: bool = False) -> Path: ...


class SandboxFactory(Protocol):
    """Create a Sandbox for a given Workspace."""

    def __call__(self, workspace: Path) -> Sandbox: ...
