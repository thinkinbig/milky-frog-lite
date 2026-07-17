from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from milky_frog.adapters.local import LocalSandbox
from milky_frog.core.sandbox import CommandResult
from milky_frog.harness.subagent_worktree import (
    SubagentWorktreeError,
    create_worktree,
    finalize_worktree,
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
