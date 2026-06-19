from __future__ import annotations

import fnmatch
import os
from pathlib import Path


class SandboxViolation(PermissionError):
    pass


class LocalSandbox:
    """Policy boundary for structured local file operations; not host isolation."""

    DEFAULT_DENY_PATTERNS = (
        ".git",
        ".git/**",
        ".env",
        ".env.*",
        "*.pem",
        "*.key",
        "**/.aws/**",
        "**/.ssh/**",
    )
    ENV_ALLOWLIST = ("HOME", "LANG", "LC_ALL", "PATH", "SHELL", "TERM", "TMPDIR")

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError(f"workspace is not a directory: {workspace}")
        self._deny_patterns = (*self.DEFAULT_DENY_PATTERNS, *self._load_ignore_file())

    def resolve(self, relative_path: str, *, allow_sensitive: bool = False) -> Path:
        candidate = (self.workspace / relative_path).resolve(strict=False)
        try:
            normalized = candidate.relative_to(self.workspace)
        except ValueError as error:
            raise SandboxViolation(f"path escapes workspace: {relative_path}") from error
        normalized_text = normalized.as_posix()
        if not allow_sensitive and self._is_denied(normalized_text):
            raise SandboxViolation(f"sensitive path requires approval: {relative_path}")
        return candidate

    def command_environment(self) -> dict[str, str]:
        return {name: os.environ[name] for name in self.ENV_ALLOWLIST if name in os.environ}

    def _is_denied(self, normalized_path: str) -> bool:
        return any(fnmatch.fnmatch(normalized_path, pattern) for pattern in self._deny_patterns)

    def _load_ignore_file(self) -> tuple[str, ...]:
        ignore_file = self.workspace / ".milkyfrogignore"
        if not ignore_file.is_file():
            return ()
        return tuple(
            line.strip()
            for line in ignore_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
