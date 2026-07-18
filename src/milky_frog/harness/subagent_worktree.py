from __future__ import annotations

import contextlib
import logging
import shlex
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from milky_frog.adapters.docker import BindMount
from milky_frog.core.cleanup import complete_cleanup
from milky_frog.core.sandbox import (
    CommandOutcome,
    CommandResult,
    CommandStartError,
    CommandTimeout,
    Sandbox,
)

_WORKTREE_TIMEOUT_SECONDS = 30.0
_SUBAGENT_REF_NAMESPACE = "subagent"
# Every commit the harness makes on a subagent's behalf (the auto-commit in
# finalize_worktree, the merge commit in merge_and_remove_worktree) uses this
# fixed identity rather than relying on the host's global git config — a
# clean environment (CI, a fresh container) has no user.name/user.email set,
# and `git commit`/`git merge --no-ff` refuse to create a commit without one.
_GIT_IDENTITY = "-c user.name=milky-frog -c user.email=milky-frog@localhost"

logger = logging.getLogger(__name__)


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
    # mkdtemp, not a fixed <tmp>/milky-frog-worktrees: that shared name is
    # predictable and was created with mkdir(exist_ok=True), so on a multi-user
    # host anyone could pre-create it — or symlink it — and choose where every
    # worktree (i.e. a copy of the repo's working tree) lands. mkdtemp gives an
    # owner-only 0700 directory with a random name instead.
    branch = f"{_SUBAGENT_REF_NAMESPACE}/{run_id}"
    await _require_branch_absent(sandbox, branch)
    root = Path(tempfile.mkdtemp(prefix="milky-frog-worktree-"))
    path = root / run_id
    quoted_path = shlex.quote(str(path))
    quoted_branch = shlex.quote(branch)
    command = f"git worktree add {quoted_path} -b {quoted_branch} HEAD"
    try:
        await _require_success(sandbox, command, action="create worktree")
        resolved = path.resolve(strict=True)
        if not resolved.is_dir():
            raise SubagentWorktreeError(f"git reported success but worktree is missing: {resolved}")

        # git writes the new branch's loose ref and reflog under these, so they
        # normally exist by now — but a repo with packed refs or
        # core.logAllRefUpdates=false may not have them, and a bind mount needs an
        # existing host path. Doing it here (where creating the worktree and branch
        # is the whole point) keeps ``git_docker_mounts`` a pure query.
        main_git_dir, _admin = _worktree_git_dirs(resolved)
        for ref_dir in _subagent_ref_dirs(main_git_dir):
            ref_dir.mkdir(parents=True, exist_ok=True)
        return SubagentWorktree(resolved, branch)
    except BaseException:
        try:
            await complete_cleanup(
                _rollback_worktree_creation(sandbox, path, branch, root),
                propagate_cancellation=False,
            )
        except BaseException:
            logger.exception("failed to roll back worktree creation at %s", path)
        raise


async def _rollback_worktree_creation(
    sandbox: Sandbox,
    path: Path,
    branch: str,
    root: Path,
) -> None:
    """Remove every artifact a failed ``git worktree add`` may have created."""
    worktree_removed = False
    try:
        outcome = await sandbox.run_command(
            f"git worktree remove --force {shlex.quote(str(path))}",
            timeout_seconds=_WORKTREE_TIMEOUT_SECONDS,
        )
    except BaseException:
        logger.exception("failed to remove partially created worktree at %s", path)
    else:
        try:
            registered = await _worktree_is_registered(sandbox, path)
        except BaseException:
            logger.exception("failed to verify worktree rollback at %s", path)
            registered = None
        worktree_removed = registered is False
        if not worktree_removed:
            logger.error(
                "failed to remove partially created worktree at %s: %s",
                path,
                _outcome_detail(outcome),
            )

    try:
        outcome = await sandbox.run_command(
            f"git branch -D {shlex.quote(branch)}",
            timeout_seconds=_WORKTREE_TIMEOUT_SECONDS,
        )
    except BaseException:
        logger.exception("failed to remove partially created worktree branch %s", branch)
    else:
        try:
            branch_exists = await _branch_exists(sandbox, branch)
        except BaseException:
            logger.exception("failed to verify worktree branch rollback for %s", branch)
            branch_exists = None
        if branch_exists is not False:
            logger.error(
                "failed to remove partially created worktree branch %s: %s",
                branch,
                _outcome_detail(outcome),
            )

    if worktree_removed:
        # ``root`` is the private, owner-only directory created immediately
        # above; a failed provision has never exposed it to a nested Run.
        shutil.rmtree(root, ignore_errors=True)


async def _worktree_is_registered(sandbox: Sandbox, path: Path) -> bool | None:
    """Return registration state, or ``None`` when git cannot answer."""
    outcome = await sandbox.run_command(
        "git worktree list --porcelain",
        timeout_seconds=_WORKTREE_TIMEOUT_SECONDS,
    )
    if not isinstance(outcome, CommandResult) or outcome.exit_code != 0:
        return None
    target = path.resolve(strict=False)
    registered = (
        Path(line.removeprefix("worktree ")).resolve(strict=False)
        for line in outcome.output.splitlines()
        if line.startswith("worktree ")
    )
    return target in registered


async def _branch_exists(sandbox: Sandbox, branch: str) -> bool | None:
    """Return branch state, or ``None`` when git cannot answer."""
    ref = shlex.quote(f"refs/heads/{branch}")
    outcome = await sandbox.run_command(
        f"git show-ref --verify --quiet {ref}",
        timeout_seconds=_WORKTREE_TIMEOUT_SECONDS,
    )
    match outcome:
        case CommandResult(exit_code=0):
            return True
        case CommandResult(exit_code=1):
            return False
        case _:
            return None


def _outcome_detail(outcome: CommandOutcome) -> str:
    match outcome:
        case CommandStartError(message=message):
            return message
        case CommandTimeout(seconds=seconds):
            return f"timed out after {seconds:g}s"
        case CommandResult(exit_code=exit_code, output=output):
            return output.strip() or f"exit code {exit_code}"


async def _require_branch_absent(sandbox: Sandbox, branch: str) -> None:
    """Refuse to provision over a branch the caller does not own."""
    branch_exists = await _branch_exists(sandbox, branch)
    match branch_exists:
        case False:
            return
        case True:
            raise SubagentWorktreeError(f"worktree branch already exists: {branch}")
        case None:
            raise SubagentWorktreeError(f"failed to inspect worktree branch {branch}")


def _worktree_git_dirs(worktree_path: Path) -> tuple[Path, Path]:
    """Resolve ``(main repo .git dir, this worktree's admin subdir)``.

    A linked worktree's ``.git`` is a pointer file (``gitdir: <main-repo>/.git/
    worktrees/<id>``) rather than a directory.
    """
    gitdir_line = (worktree_path / ".git").read_text(encoding="utf-8").strip()
    prefix = "gitdir:"
    if not gitdir_line.startswith(prefix):
        raise SubagentWorktreeError(f"unexpected worktree .git pointer: {gitdir_line!r}")
    worktree_admin_dir = Path(gitdir_line[len(prefix) :].strip()).resolve(strict=True)
    return worktree_admin_dir.parent.parent, worktree_admin_dir


def _subagent_ref_dirs(main_git_dir: Path) -> tuple[Path, Path]:
    """The ref and reflog directories scoping every subagent branch."""
    return (
        main_git_dir / "refs" / "heads" / _SUBAGENT_REF_NAMESPACE,
        main_git_dir / "logs" / "refs" / "heads" / _SUBAGENT_REF_NAMESPACE,
    )


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

    A pure query: ``create_worktree`` has already prepared every path named
    here, so calling this never mutates the repository.
    """
    main_git_dir, worktree_admin_dir = _worktree_git_dirs(worktree.path)
    subagent_refs_dir, subagent_reflogs_dir = _subagent_ref_dirs(main_git_dir)
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
            # Ask before aborting — `merge --abort` clears the conflicted index.
            conflicted = await _has_unmerged_paths(sandbox)
            await sandbox.run_command(
                "git merge --abort", timeout_seconds=_WORKTREE_TIMEOUT_SECONDS
            )
            detail = output.strip() or f"exit code {exit_code}"
            if not conflicted:
                # A merge that never started: unknown branch, unborn HEAD, or a
                # dirty parent tree. Raising MergeConflictError here would make
                # MergeWorktreeTool offer a conflict-resolution subagent for a
                # conflict that does not exist.
                raise SubagentWorktreeError(f"failed to merge {branch}: {detail}")
            raise MergeConflictError(
                f"merge conflict on {branch}, aborted ({detail}); "
                f"worktree preserved at {worktree} for manual resolution",
                worktree=worktree,
                branch=branch,
            )

    await _remove_worktree_dir(sandbox, worktree, action="remove merged worktree")


async def _has_unmerged_paths(sandbox: Sandbox) -> bool:
    """Whether the index holds conflicted paths — i.e. a real content conflict.

    ``git merge`` exits non-zero both for a genuine conflict and for a merge
    that never started (unknown branch, unborn HEAD, dirty working tree). Only
    the former leaves unmerged entries in the index, so only the former has
    anything for a human — or a resolution subagent — to resolve.
    """
    outcome = await sandbox.run_command(
        "git ls-files --unmerged", timeout_seconds=_WORKTREE_TIMEOUT_SECONDS
    )
    match outcome:
        case CommandResult(exit_code=0, output=output):
            return bool(output.strip())
        case _:
            return False


async def finalize_worktree(
    sandbox: Sandbox,
    worktree: SubagentWorktree,
) -> WorktreeOutcome:
    """Preserve a worktree that produced work; remove one that produced none.

    Two ways a subagent leaves work behind, and both must be kept:

    - **Uncommitted.** It may exhaust its model-call budget after writing files
      but before running ``git commit`` — changes that ``merge_worktree``'s
      ``git merge --no-ff`` would silently skip, since that merges committed
      history, not the working tree. Committing here guarantees they are
      reachable from the branch tip before any merge is attempted.
    - **Already committed.** A well-behaved subagent commits its own work, so
      the tree is clean. Deciding on ``status --porcelain`` alone would call
      that "nothing to do", delete the worktree, and skip the merge follow-up —
      silently dropping the subagent's entire output.

    So the kept/removed decision is "is the branch ahead of the parent's HEAD",
    which covers both; a dirty tree is just one way to get there.
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

    if await _commits_ahead_of_head(sandbox, worktree.branch):
        return WorktreeOutcome(worktree, kept=True)

    await _remove_worktree_dir(sandbox, worktree.path, action="remove clean worktree")
    return WorktreeOutcome(worktree, kept=False)


async def _remove_worktree_dir(sandbox: Sandbox, path: Path, *, action: str) -> None:
    """Remove a worktree, then drop the private temp root ``create_worktree`` made."""
    await _require_success(
        sandbox,
        f"git worktree remove {shlex.quote(str(path))}",
        action=action,
    )
    # That root holds nothing but this one worktree and is owner-only, so it is
    # ours to remove — but never at the cost of failing the removal itself.
    with contextlib.suppress(OSError):
        path.parent.rmdir()


async def _commits_ahead_of_head(sandbox: Sandbox, branch: str) -> bool:
    """Whether ``branch`` carries commits the parent Workspace's HEAD lacks.

    ``sandbox`` targets the parent Workspace, so ``HEAD`` here is the merge
    destination — the same ref ``merge_and_remove_worktree`` merges into.
    """
    counted = await _require_success(
        sandbox,
        f"git rev-list --count {shlex.quote(branch)} --not HEAD",
        action="count worktree commits",
    )
    tail = counted.output.strip().splitlines()
    if not tail:
        return False
    try:
        return int(tail[-1].strip()) > 0
    except ValueError as error:
        raise SubagentWorktreeError(
            f"could not read commit count for {branch}: {counted.output.strip()!r}"
        ) from error


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
