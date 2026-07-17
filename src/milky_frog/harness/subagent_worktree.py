from __future__ import annotations

import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path

from milky_frog.adapters.docker import BindMount
from milky_frog.core.sandbox import CommandResult, CommandStartError, CommandTimeout, Sandbox

_WORKTREE_TIMEOUT_SECONDS = 30.0
_SUBAGENT_REF_NAMESPACE = "subagent"
# Every commit the harness makes on a subagent's behalf (the auto-commit in
# finalize_worktree, the merge commit in merge_and_remove_worktree) uses this
# fixed identity rather than relying on the host's global git config — a
# clean environment (CI, a fresh container) has no user.name/user.email set,
# and `git commit`/`git merge --no-ff` refuse to create a commit without one.
_GIT_IDENTITY = "-c user.name=milky-frog -c user.email=milky-frog@localhost"


class SubagentWorktreeError(RuntimeError):
    """A git worktree lifecycle command failed."""


class MergeConflictError(SubagentWorktreeError):
    """A merge was aborted due to a real content conflict, not a plumbing failure.

    Distinct from the base error so callers (``MergeWorktreeTool``) can offer a
    conflict-specific follow-up — e.g. delegating resolution to a fresh
    subagent — without string-matching the error message.
    """

    def __init__(self, message: str, *, worktree: Path, branch: str) -> None:
        super().__init__(message)
        self.worktree = worktree
        self.branch = branch


@dataclass(frozen=True, slots=True)
class SubagentWorktree:
    path: Path
    branch: str


@dataclass(frozen=True, slots=True)
class WorktreeOutcome:
    worktree: SubagentWorktree
    kept: bool


async def create_worktree(
    sandbox: Sandbox,
    base_workspace: Path,
    run_id: str,
) -> SubagentWorktree:
    """Create a linked worktree and dedicated branch for one writable nested Run."""
    base = base_workspace.resolve(strict=True)
    if sandbox.workspace != base:
        raise SubagentWorktreeError(
            f"worktree management Sandbox must target the parent Workspace: {base}"
        )
    root = Path(tempfile.gettempdir()) / "milky-frog-worktrees"
    root.mkdir(parents=True, exist_ok=True)
    path = root / run_id
    branch = f"subagent/{run_id}"
    quoted_path = shlex.quote(str(path))
    quoted_branch = shlex.quote(branch)
    command = f"git worktree add {quoted_path} -b {quoted_branch} HEAD"
    await _require_success(sandbox, command, action="create worktree")
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise SubagentWorktreeError(f"git reported success but worktree is missing: {resolved}")
    return SubagentWorktree(resolved, branch)


def git_docker_mounts(worktree: SubagentWorktree) -> list[BindMount]:
    """Bind mounts letting git work inside a Container Sandbox for this worktree.

    A linked worktree's ``.git`` is a pointer file (``gitdir: <main-repo>/.git/
    worktrees/<id>``) into the *main* repository's admin directory — a host
    path entirely outside the worktree's own directory tree. Bind-mounting
    only the worktree (as ``DockerSandbox`` normally does for the Workspace)
    leaves that pointer dangling inside the container, so every git command
    fails with "not a git repository".

    Mounts the whole main ``.git`` read-only (so git can resolve history,
    config, and other branches' refs) and overlays only what this worktree's
    branch actually needs to write: the shared object database, this
    worktree's own admin subdir (its HEAD/index/reflog), and the
    ``refs/heads/subagent/`` namespace that ``create_worktree`` scopes every
    subagent branch under — never the parent's own branch refs.
    """
    gitdir_line = (worktree.path / ".git").read_text(encoding="utf-8").strip()
    prefix = "gitdir:"
    if not gitdir_line.startswith(prefix):
        raise SubagentWorktreeError(f"unexpected worktree .git pointer: {gitdir_line!r}")
    worktree_admin_dir = Path(gitdir_line[len(prefix) :].strip()).resolve(strict=True)
    main_git_dir = worktree_admin_dir.parent.parent

    subagent_refs_dir = main_git_dir / "refs" / "heads" / _SUBAGENT_REF_NAMESPACE
    subagent_reflogs_dir = main_git_dir / "logs" / "refs" / "heads" / _SUBAGENT_REF_NAMESPACE
    subagent_refs_dir.mkdir(parents=True, exist_ok=True)
    subagent_reflogs_dir.mkdir(parents=True, exist_ok=True)

    return [
        BindMount(str(main_git_dir), read_only=True),
        BindMount(str(main_git_dir / "objects")),
        BindMount(str(worktree_admin_dir)),
        BindMount(str(subagent_refs_dir)),
        BindMount(str(subagent_reflogs_dir)),
    ]


async def merge_and_remove_worktree(
    sandbox: Sandbox,
    worktree: Path,
    branch: str,
) -> None:
    """Merge ``branch`` into the parent's current HEAD and remove the worktree.

    ``sandbox`` must target the parent Workspace (the merge destination), not
    the worktree itself. On conflict, aborts the merge and preserves the
    worktree untouched for manual resolution — merging is never retried or
    resolved automatically, matching ``finalize_worktree``'s "never destroy or
    silently resolve unreviewed work" principle.
    """
    quoted_branch = shlex.quote(branch)
    outcome = await sandbox.run_command(
        f"git {_GIT_IDENTITY} merge --no-ff {quoted_branch}",
        timeout_seconds=_WORKTREE_TIMEOUT_SECONDS,
    )
    match outcome:
        case CommandStartError(message=message):
            raise SubagentWorktreeError(f"failed to merge {branch}: {message}")
        case CommandTimeout(seconds=seconds):
            raise SubagentWorktreeError(f"failed to merge {branch}: timed out after {seconds:g}s")
        case CommandResult(exit_code=exit_code, output=output) if exit_code != 0:
            await sandbox.run_command(
                "git merge --abort", timeout_seconds=_WORKTREE_TIMEOUT_SECONDS
            )
            detail = output.strip() or f"exit code {exit_code}"
            raise MergeConflictError(
                f"merge conflict on {branch}, aborted ({detail}); "
                f"worktree preserved at {worktree} for manual resolution",
                worktree=worktree,
                branch=branch,
            )

    await _require_success(
        sandbox,
        f"git worktree remove {shlex.quote(str(worktree))}",
        action="remove merged worktree",
    )


async def finalize_worktree(
    sandbox: Sandbox,
    worktree: SubagentWorktree,
) -> WorktreeOutcome:
    """Remove a clean worktree; commit and preserve a dirty one for parent-Run review.

    A write-capability subagent may exhaust its model-call budget after
    writing files but before running ``git commit`` itself — leaving changes
    on disk that ``merge_worktree``'s ``git merge --no-ff`` would silently
    skip, since that command merges committed history on the branch, not the
    working tree. Committing here (rather than relying on the subagent to do
    it) guarantees any written changes are reachable from the branch tip
    before a merge is ever attempted.
    """
    quoted_path = shlex.quote(str(worktree.path))
    status = await _require_success(
        sandbox,
        f"git -C {quoted_path} status --porcelain",
        action="inspect worktree",
    )
    if status.output.strip():
        await _require_success(
            sandbox,
            f"git -C {quoted_path} add -A",
            action="stage worktree changes",
        )
        await _require_success(
            sandbox,
            f"git -C {quoted_path} {_GIT_IDENTITY} "
            f"commit -m {shlex.quote(f'subagent: {worktree.branch}')}",
            action="commit worktree changes",
        )
        return WorktreeOutcome(worktree, kept=True)

    await _require_success(
        sandbox,
        f"git worktree remove {shlex.quote(str(worktree.path))}",
        action="remove clean worktree",
    )
    return WorktreeOutcome(worktree, kept=False)


async def _require_success(sandbox: Sandbox, command: str, *, action: str) -> CommandResult:
    outcome = await sandbox.run_command(command, timeout_seconds=_WORKTREE_TIMEOUT_SECONDS)
    match outcome:
        case CommandStartError(message=message):
            raise SubagentWorktreeError(f"failed to {action}: {message}")
        case CommandTimeout(seconds=seconds):
            raise SubagentWorktreeError(f"failed to {action}: timed out after {seconds:g}s")
        case CommandResult(exit_code=exit_code, output=output):
            if exit_code != 0:
                detail = output.strip() or f"exit code {exit_code}"
                raise SubagentWorktreeError(f"failed to {action}: {detail}")
            return outcome
