from __future__ import annotations

import stat
from pathlib import Path
from uuid import uuid4

import pytest

from milky_frog.adapters.local import LocalSandbox
from milky_frog.core.sandbox import CommandResult
from milky_frog.harness.subagent_worktree import (
    MergeConflictError,
    SubagentWorktreeError,
    create_worktree,
    finalize_worktree,
    git_docker_mounts,
    merge_and_remove_worktree,
)


async def _git(sandbox: LocalSandbox, command: str) -> None:
    outcome = await sandbox.run_command(command, timeout_seconds=10)
    assert isinstance(outcome, CommandResult)
    assert outcome.exit_code == 0, outcome.output


@pytest.fixture
def git_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    return workspace


@pytest.mark.asyncio
async def test_clean_worktree_is_removed(git_workspace: Path) -> None:
    sandbox = LocalSandbox(git_workspace)
    await _git(
        sandbox,
        "git init && git -c user.name=test -c user.email=test@example.com "
        "commit --allow-empty -m init",
    )

    worktree = await create_worktree(sandbox, git_workspace, "clean-run")
    outcome = await finalize_worktree(sandbox, worktree)

    assert outcome.kept is False
    assert not worktree.path.exists()


@pytest.mark.asyncio
async def test_dirty_worktree_is_preserved(git_workspace: Path) -> None:
    sandbox = LocalSandbox(git_workspace)
    await _git(
        sandbox,
        "git init && git -c user.name=test -c user.email=test@example.com "
        "commit --allow-empty -m init",
    )

    run_id = f"dirty-{uuid4().hex}"
    worktree = await create_worktree(sandbox, git_workspace, run_id)
    (worktree.path / "change.txt").write_text("keep me", encoding="utf-8")
    outcome = await finalize_worktree(sandbox, worktree)

    assert outcome.kept is True
    assert worktree.path.is_dir()
    assert worktree.branch == f"subagent/{run_id}"

    # A subagent that never got around to `git commit` must still land its
    # changes on the branch — otherwise `merge_worktree`'s `git merge --no-ff`
    # has nothing to merge and silently drops the work.
    status = await sandbox.run_command(
        f"git -C {worktree.path} status --porcelain", timeout_seconds=10
    )
    assert isinstance(status, CommandResult)
    assert status.output.strip() == ""

    await _git(sandbox, f"git worktree remove --force {worktree.path}")


@pytest.mark.asyncio
async def test_finalize_then_merge_lands_uncommitted_changes(git_workspace: Path) -> None:
    sandbox = LocalSandbox(git_workspace)
    await _git(
        sandbox,
        "git init && git -c user.name=test -c user.email=test@example.com "
        "commit --allow-empty -m init",
    )

    run_id = f"autocommit-{uuid4().hex}"
    worktree = await create_worktree(sandbox, git_workspace, run_id)
    (worktree.path / "change.txt").write_text("written by subagent", encoding="utf-8")

    outcome = await finalize_worktree(sandbox, worktree)
    assert outcome.kept is True

    await merge_and_remove_worktree(sandbox, worktree.path, worktree.branch)

    assert not worktree.path.exists()
    assert (git_workspace / "change.txt").read_text(encoding="utf-8") == "written by subagent"


@pytest.mark.asyncio
async def test_merge_and_remove_worktree_merges_cleanly(git_workspace: Path) -> None:
    sandbox = LocalSandbox(git_workspace)
    await _git(
        sandbox,
        "git init && git -c user.name=test -c user.email=test@example.com "
        "commit --allow-empty -m init",
    )

    run_id = f"merge-{uuid4().hex}"
    worktree = await create_worktree(sandbox, git_workspace, run_id)
    (worktree.path / "change.txt").write_text("hello", encoding="utf-8")
    await _git(
        LocalSandbox(worktree.path),
        "git add change.txt && git -c user.name=test -c user.email=test@example.com "
        "commit -m change",
    )

    await merge_and_remove_worktree(sandbox, worktree.path, worktree.branch)

    assert not worktree.path.exists()
    assert (git_workspace / "change.txt").read_text(encoding="utf-8") == "hello"


@pytest.mark.asyncio
async def test_merge_and_remove_worktree_aborts_and_preserves_on_conflict(
    git_workspace: Path,
) -> None:
    sandbox = LocalSandbox(git_workspace)
    await _git(
        sandbox,
        "git init && git -c user.name=test -c user.email=test@example.com "
        "commit --allow-empty -m init",
    )
    (git_workspace / "change.txt").write_text("base", encoding="utf-8")
    await _git(
        sandbox,
        "git add change.txt && git -c user.name=test -c user.email=test@example.com "
        "commit -m base-change",
    )

    run_id = f"conflict-{uuid4().hex}"
    worktree = await create_worktree(sandbox, git_workspace, run_id)

    # Diverge *after* branching so parent and subagent each edit the same line
    # independently — otherwise the subagent branch just fast-forwards.
    (git_workspace / "change.txt").write_text("from parent", encoding="utf-8")
    await _git(
        sandbox,
        "git add change.txt && git -c user.name=test -c user.email=test@example.com "
        "commit -m parent-change",
    )
    (worktree.path / "change.txt").write_text("from subagent", encoding="utf-8")
    await _git(
        LocalSandbox(worktree.path),
        "git add change.txt && git -c user.name=test -c user.email=test@example.com "
        "commit -m subagent-change",
    )

    with pytest.raises(SubagentWorktreeError, match="merge conflict"):
        await merge_and_remove_worktree(sandbox, worktree.path, worktree.branch)

    assert worktree.path.is_dir()
    assert (git_workspace / "change.txt").read_text(encoding="utf-8") == "from parent"

    await _git(sandbox, f"git worktree remove --force {worktree.path}")


@pytest.mark.asyncio
async def test_git_docker_mounts_exposes_only_what_the_branch_needs(git_workspace: Path) -> None:
    sandbox = LocalSandbox(git_workspace)
    await _git(
        sandbox,
        "git init && git -c user.name=test -c user.email=test@example.com "
        "commit --allow-empty -m init",
    )
    run_id = f"mounts-{uuid4().hex}"
    worktree = await create_worktree(sandbox, git_workspace, run_id)
    main_git_dir = (git_workspace / ".git").resolve()

    mounts = git_docker_mounts(worktree)

    assert mounts[0].host_path == str(main_git_dir)
    assert mounts[0].read_only is True

    writable_paths = {m.host_path for m in mounts[1:]}
    assert all(not m.read_only for m in mounts[1:])
    assert str(main_git_dir / "objects") in writable_paths
    assert str(main_git_dir / "worktrees" / run_id) in writable_paths
    assert str(main_git_dir / "refs" / "heads" / "subagent") in writable_paths
    assert str(main_git_dir / "logs" / "refs" / "heads" / "subagent") in writable_paths

    # The writable ref/reflog namespace dirs must exist on disk before Docker
    # tries to bind-mount them, or `docker run` fails with "no such file".
    assert (main_git_dir / "refs" / "heads" / "subagent").is_dir()
    assert (main_git_dir / "logs" / "refs" / "heads" / "subagent").is_dir()
    # Never the parent's own branch namespace.
    assert str(main_git_dir / "refs" / "heads") not in writable_paths

    await _git(sandbox, f"git worktree remove --force {worktree.path}")


@pytest.mark.asyncio
async def test_worktree_committed_by_the_subagent_itself_is_preserved(
    git_workspace: Path,
) -> None:
    """A subagent that commits its own work leaves a CLEAN tree — still keep it.

    Deciding on `git status --porcelain` alone calls this "nothing to do",
    removes the worktree, and reports kept=False, so ``SubagentTool`` never
    synthesizes the ``merge_worktree`` follow-up — silently dropping the
    subagent's entire output while its branch is orphaned in .git.
    """
    sandbox = LocalSandbox(git_workspace)
    await _git(
        sandbox,
        "git init && git -c user.name=test -c user.email=test@example.com "
        "commit --allow-empty -m init",
    )

    run_id = f"committed-{uuid4().hex}"
    worktree = await create_worktree(sandbox, git_workspace, run_id)
    (worktree.path / "feature.txt").write_text("subagent work", encoding="utf-8")
    await _git(
        sandbox,
        f"git -C {worktree.path} add -A && git -C {worktree.path} "
        "-c user.name=sub -c user.email=sub@example.com commit -m 'subagent: done'",
    )

    outcome = await finalize_worktree(sandbox, worktree)

    assert outcome.kept is True
    assert worktree.path.is_dir()

    # And the work is actually mergeable into the parent.
    await merge_and_remove_worktree(sandbox, worktree.path, worktree.branch)
    assert (git_workspace / "feature.txt").read_text(encoding="utf-8") == "subagent work"


@pytest.mark.asyncio
async def test_merge_failure_that_is_not_a_conflict_is_not_a_conflict_error(
    git_workspace: Path,
) -> None:
    """An unmergeable ref exits non-zero with no unmerged paths.

    Reporting that as ``MergeConflictError`` makes ``MergeWorktreeTool`` offer a
    conflict-resolution subagent for a conflict that does not exist.
    """
    sandbox = LocalSandbox(git_workspace)
    await _git(
        sandbox,
        "git init && git -c user.name=test -c user.email=test@example.com "
        "commit --allow-empty -m init",
    )

    with pytest.raises(SubagentWorktreeError) as caught:
        await merge_and_remove_worktree(sandbox, git_workspace / "nowhere", "subagent/missing")

    assert not isinstance(caught.value, MergeConflictError)


@pytest.mark.asyncio
async def test_worktree_root_is_private_and_cleaned_up(git_workspace: Path) -> None:
    """Each worktree gets its own owner-only temp root, removed with the worktree.

    A fixed <tmp>/milky-frog-worktrees path is predictable and was created with
    mkdir(exist_ok=True), so on a shared host another user could pre-create or
    symlink it and choose where the repo's working tree gets copied.
    """
    sandbox = LocalSandbox(git_workspace)
    await _git(
        sandbox,
        "git init && git -c user.name=test -c user.email=test@example.com "
        "commit --allow-empty -m init",
    )

    worktree = await create_worktree(sandbox, git_workspace, f"private-{uuid4().hex}")
    root = worktree.path.parent
    assert stat.S_IMODE(root.stat().st_mode) == 0o700

    await finalize_worktree(sandbox, worktree)

    assert not worktree.path.exists()
    assert not root.exists()


def test_git_docker_mounts_does_not_mutate_the_repository(git_workspace: Path) -> None:
    """Building mounts is a pure query — create_worktree already prepared the dirs."""
    import asyncio

    async def build() -> None:
        sandbox = LocalSandbox(git_workspace)
        await _git(
            sandbox,
            "git init && git -c user.name=test -c user.email=test@example.com "
            "commit --allow-empty -m init",
        )
        worktree = await create_worktree(sandbox, git_workspace, f"pure-{uuid4().hex}")
        git_dir = git_workspace / ".git"
        before = sorted(p.relative_to(git_dir) for p in git_dir.rglob("*"))

        git_docker_mounts(worktree)

        assert sorted(p.relative_to(git_dir) for p in git_dir.rglob("*")) == before
        await _git(sandbox, f"git worktree remove --force {worktree.path}")

    asyncio.run(build())
