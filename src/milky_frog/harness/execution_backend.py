"""Execution Backend seam — injectable local-or-Docker execution context.

Unifies three concerns that all break when moving from local to Docker:

- **Path resolution** — workspace-relative → absolute Path (with deny patterns)
- **Environment building** — subprocess env dict (allowlist + non-interactive defaults)
- **(future) Command execution** — ``create_subprocess_shell`` → ``docker exec``

`ToolContext` carries one ``backend`` field instead of separate ``sandbox`` +
``command_env``.

See ADR-0016 for the full rationale.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Protocol

from milky_frog.project import ProjectConfig

# Host env vars passed through to a spawned local command (never secrets).
_COMMAND_ENV_ALLOWLIST = ("HOME", "LANG", "LC_ALL", "PATH", "SHELL", "TERM", "TMPDIR")

# Non-interactive defaults for PTY-backed commands.  PTY makes stdout a TTY, so git
# and other tools may spawn pagers (less) or block on prompts unless overridden.
_NONINTERACTIVE_ENV: dict[str, str] = {
    "CI": "true",
    "PAGER": "cat",
    "GIT_PAGER": "cat",
    "MANPAGER": "cat",
    "GIT_TERMINAL_PROMPT": "0",
    "BROWSER": "cat",
    # Belt-and-suspenders when ~/.gitconfig sets core.pager despite GIT_PAGER.
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "core.pager",
    "GIT_CONFIG_VALUE_0": "cat",
}


class SandboxViolation(PermissionError):
    """Raised when a path resolution violates the Execution Backend policy.

    Renamed from the old ``infra.sandbox.local.SandboxViolation`` — the
    exception semantics are unchanged.
    """


class ExecutionBackend(Protocol):
    """Policy boundary for Workspace execution.

    Wraps path resolution, environment building, and (in future) command
    execution — the three concerns that change when running inside a container.
    """

    workspace: Path

    def resolve(self, relative_path: str, *, allow_sensitive: bool = False) -> Path:
        """Resolve a workspace-relative path, enforcing deny-pattern policy.

        Raises ``SandboxViolation`` if the path escapes the workspace or
        matches a deny pattern.
        """
        ...

    def build_env(self) -> dict[str, str]:
        """Return the final environment dict for a spawned local command.

        The returned dict is ready to pass directly to
        ``create_subprocess_shell`` — the consumer must not post-process it.
        """
        ...


class ExecutionBackendFactory(Protocol):
    """Create an ExecutionBackend for a given Workspace."""

    def __call__(self, workspace: Path) -> ExecutionBackend: ...


class LocalExecutionBackend:
    """Default ExecutionBackend: local filesystem + host env + PTY subprocess.

    Path policy
    -----------
    Denies access to dot-directories (``.git``, ``.ssh``, …), secret files
    (``.env``, ``*.pem``, ``*.key``), and AWS config.  Custom deny patterns
    can be added via ``.milkyfrogignore`` in the workspace root.

    Environment
    -----------
    Build order:
      1. Start with ``_NONINTERACTIVE_ENV`` (disables pagers / prompts).
      2. Overlay host environ entries matching the allowlist (built-in
         ``_COMMAND_ENV_ALLOWLIST`` + optional extras from
         ``ProjectConfig.env_allowlist_extra``).
      3. Ensure ``/usr/local/bin`` is on ``PATH``.

    ``env_allowlist_extra`` is the config-driven hook that lets workspace
    owners forward additional env vars (e.g. ``MY_BUILD_VAR``) to
    subprocesses without code changes.
    """

    __slots__ = ("_allowlist", "_deny_patterns", "workspace")

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

        # Deny patterns
        self._deny_patterns = (*self.DEFAULT_DENY_PATTERNS, *self._load_ignore_file())

        # Env allowlist
        extra: tuple[str, ...] = config.env_allowlist_extra if config is not None else ()
        self._allowlist = _COMMAND_ENV_ALLOWLIST + extra

    # ── Path resolution ──────────────────────────────────────────────

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

    # ── Environment building ─────────────────────────────────────────

    def build_env(self) -> dict[str, str]:
        env = dict(_NONINTERACTIVE_ENV)
        env.update({name: os.environ[name] for name in self._allowlist if name in os.environ})
        path = env.get("PATH", "")
        if "/usr/local/bin" not in path:
            env["PATH"] = f"/usr/local/bin:{path}" if path else "/usr/local/bin"
        return env


def default_execution_backend(
    workspace: Path, config: ProjectConfig | None = None
) -> ExecutionBackend:
    """Return a default ``LocalExecutionBackend`` for the given workspace."""
    return LocalExecutionBackend(workspace, config)
