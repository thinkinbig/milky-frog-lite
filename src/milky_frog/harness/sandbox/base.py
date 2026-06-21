from __future__ import annotations

from pathlib import Path
from typing import Protocol


class Sandbox(Protocol):
    """Policy boundary for structured Workspace operations."""

    workspace: Path

    def resolve(self, relative_path: str, *, allow_sensitive: bool = False) -> Path: ...

    def command_environment(self) -> dict[str, str]: ...
