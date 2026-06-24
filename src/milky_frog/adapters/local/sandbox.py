from __future__ import annotations

import fnmatch
import os
from pathlib import Path

from milky_frog.core.sandbox import SandboxViolation
from milky_frog.project import ProjectConfig, load_project_config

_COMMAND_ENV_ALLOWLIST = ("HOME", "LANG", "LC_ALL", "PATH", "SHELL", "TERM", "TMPDIR")

_NONINTERACTIVE_ENV: dict[str, str] = {
    "CI": "true",
    "PAGER": "cat",
    "GIT_PAGER": "cat",
    "MANPAGER": "cat",
    "GIT_TERMINAL_PROMPT": "0",
    "BROWSER": "cat",
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "core.pager",
    "GIT_CONFIG_VALUE_0": "cat",
}


class LocalSandbox:
    """Default Sandbox adapter: local filesystem + host env + PTY subprocess."""

    __slots__ = ("_allowlist", "_deny_patterns", "config", "workspace")

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

    def __init__(self, workspace: Path, config: ProjectConfig | None = None) -> None:
        self.workspace = workspace.resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError(f"workspace is not a directory: {workspace}")

        self.config = config if config is not None else load_project_config(self.workspace)
        self._deny_patterns = (*self.DEFAULT_DENY_PATTERNS, *self._load_ignore_file())
        self._allowlist = _COMMAND_ENV_ALLOWLIST + self.config.env_allowlist_extra

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

    def build_env(self) -> dict[str, str]:
        env = {name: os.environ[name] for name in self._allowlist if name in os.environ}
        env.update(_NONINTERACTIVE_ENV)
        path = env.get("PATH", "")
        if "/usr/local/bin" not in path:
            env["PATH"] = f"/usr/local/bin:{path}" if path else "/usr/local/bin"
        return env
