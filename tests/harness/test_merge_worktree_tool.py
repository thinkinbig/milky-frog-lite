from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from milky_frog.adapters.local import LocalSandbox
from milky_frog.core.sandbox import CommandResult
from milky_frog.harness.subagent_worktree import create_worktree
from milky_frog.harness.tools import ToolContext
from milky_frog.harness.tools.builtins.merge_worktree import MergeWorktreeInput, MergeWorktreeTool


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
async def test_execute_merges_dirty_worktree_and_removes_it(git_workspace: Path) -> None:
    sandbox = LocalSandbox(git_workspace)
    await _git(
        sandbox,
        "git init && git -c user.name=test -c user.email=test@example.com "
        "commit --allow-empty -m init",
    )
    run_id = f"tool-{uuid4().hex}"
    worktree = await create_worktree(sandbox, git_workspace, run_id)
    (worktree.path / "change.txt").write_text("hello", encoding="utf-8")
    await _git(
        LocalSandbox(worktree.path),
        "git add change.txt && git -c user.name=test -c user.email=test@example.com "
        "commit -m change",
    )

    tool = MergeWorktreeTool()
    context = ToolContext("run-1", git_workspace, sandbox=sandbox)

    result = await tool.execute(
        context, MergeWorktreeInput(worktree=str(worktree.path), branch=worktree.branch)
    )

    assert result.is_error is False
    assert not worktree.path.exists()
    assert (git_workspace / "change.txt").read_text(encoding="utf-8") == "hello"


@pytest.mark.asyncio
async def test_execute_reports_conflict_and_preserves_worktree(git_workspace: Path) -> None:
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
    run_id = f"tool-conflict-{uuid4().hex}"
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

    tool = MergeWorktreeTool()
    context = ToolContext("run-1", git_workspace, sandbox=sandbox)

    result = await tool.execute(
        context, MergeWorktreeInput(worktree=str(worktree.path), branch=worktree.branch)
    )

    assert result.is_error is True
    assert "merge conflict" in result.content
    assert worktree.path.is_dir()

    # A real conflict deterministically offers to delegate resolution to a
    # fresh write-capability subagent — not just a passive error string.
    assert result.follow_up is not None
    assert result.follow_up.tool_name == "subagent"
    assert result.follow_up.arguments["capability"] == "write"
    assert worktree.branch in result.follow_up.arguments["prompt"]
    assert str(worktree.path) in result.follow_up.arguments["prompt"]

    await _git(sandbox, f"git worktree remove --force {worktree.path}")


@pytest.mark.asyncio
async def test_execute_sets_no_follow_up_on_clean_merge(git_workspace: Path) -> None:
    sandbox = LocalSandbox(git_workspace)
    await _git(
        sandbox,
        "git init && git -c user.name=test -c user.email=test@example.com "
        "commit --allow-empty -m init",
    )
    run_id = f"tool-clean-{uuid4().hex}"
    worktree = await create_worktree(sandbox, git_workspace, run_id)
    (worktree.path / "change.txt").write_text("hello", encoding="utf-8")
    await _git(
        LocalSandbox(worktree.path),
        "git add change.txt && git -c user.name=test -c user.email=test@example.com "
        "commit -m change",
    )

    tool = MergeWorktreeTool()
    context = ToolContext("run-1", git_workspace, sandbox=sandbox)

    result = await tool.execute(
        context, MergeWorktreeInput(worktree=str(worktree.path), branch=worktree.branch)
    )

    assert result.follow_up is None
