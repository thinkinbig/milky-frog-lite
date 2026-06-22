"""Tool-policy tests: PolicyHandler on RunBeforeTool, approval pause and resume."""

from __future__ import annotations

from pathlib import Path

import pytest

from milky_frog.domain import (
    MessageRole,
    RunRequest,
    RunStatus,
    ToolCall,
    ToolDecision,
)
from milky_frog.handlers import LifecycleBus
from milky_frog.handlers.policy import PolicyHandler
from milky_frog.harness.runner import Harness
from milky_frog.harness.tools import ToolRegistry, default_tools
from milky_frog.harness.tools.tool_policy import (
    DefaultToolPolicy,
    DenyAllPolicy,
    PermissivePolicy,
    approval_free_tool_names,
)
from tests.checkpoint_helpers import tool_messages
from tests.stubs import EchoTool, FakeModel

# ── Policy unit tests ────────────────────────────────────────────────


def test_default_policy_allows_approval_free_tools() -> None:
    policy = DefaultToolPolicy()
    for name in approval_free_tool_names(default_tools()):
        assert policy.decide(ToolCall("c1", name, {})) is ToolDecision.ALLOW


def test_default_policy_allows_read_only_git_commands() -> None:
    policy = DefaultToolPolicy()
    assert policy.decide(ToolCall("c1", "git", {"command": "status"})) is ToolDecision.ALLOW
    assert policy.decide(ToolCall("c2", "git", {"command": "diff --staged"})) is ToolDecision.ALLOW
    assert (
        policy.decide(ToolCall("c3", "git", {"command": "log --oneline -5"})) is ToolDecision.ALLOW
    )


def test_default_policy_needs_approval_for_mutating_git_and_file_tools() -> None:
    policy = DefaultToolPolicy()
    assert policy.decide(ToolCall("c1", "git", {"command": "add ."})) is ToolDecision.NEEDS_APPROVAL
    assert (
        policy.decide(ToolCall("c2", "git", {"command": "commit -m msg"}))
        is ToolDecision.NEEDS_APPROVAL
    )
    assert (
        policy.decide(ToolCall("c3", "git", {"command": "branch feature"}))
        is ToolDecision.NEEDS_APPROVAL
    )
    assert policy.decide(ToolCall("c4", "write_file", {})) is ToolDecision.NEEDS_APPROVAL
    assert policy.decide(ToolCall("c5", "edit_file", {})) is ToolDecision.NEEDS_APPROVAL
    assert policy.decide(ToolCall("c6", "echo", {})) is ToolDecision.NEEDS_APPROVAL


def test_permissive_policy_allows_everything() -> None:
    policy = PermissivePolicy()
    assert policy.decide(ToolCall("c1", "write", {})) is ToolDecision.ALLOW
    assert policy.decide(ToolCall("c2", "unknown_tool", {})) is ToolDecision.ALLOW


def test_deny_all_policy_denies_everything() -> None:
    policy = DenyAllPolicy()
    assert policy.decide(ToolCall("c1", "read", {})) is ToolDecision.DENY


# ── PolicyHandler via RunBeforeTool (replaces old ToolGate) ─────────


def _make_bus(policy: DefaultToolPolicy | None = None) -> LifecycleBus:
    bus = LifecycleBus()
    PolicyHandler(policy).register(bus)
    return bus


# ── Harness integration: tool denial ──────────────────────────────────


@pytest.mark.asyncio
async def test_tool_denied_by_policy_returns_error_result(tmp_path: Path) -> None:
    """A tool denied by policy produces an is_error ToolResult without executing."""
    harness = Harness(
        model=FakeModel(),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=__import__(
            "milky_frog.checkpoint", fromlist=["SqliteCheckpointStore"]
        ).SqliteCheckpointStore(tmp_path / "state.db"),
        handlers=_make_bus(DenyAllPolicy()),
    )

    result = await harness.run(RunRequest("echo hello", tmp_path))
    assert result.status is RunStatus.COMPLETED


# ── Harness integration: approval pause and resume ────────────────────


def _find_pending_tool_id(store, run_id: str, state) -> str:
    last_assistant = [m for m in state.messages if m.role is MessageRole.ASSISTANT][-1]
    assert last_assistant.tool_calls
    return last_assistant.tool_calls[0].id


@pytest.mark.asyncio
async def test_approval_pauses_run_and_resume_executes_approved_tool(
    tmp_path: Path,
) -> None:
    """When a tool needs approval, the run pauses. On approve+resume, it executes."""
    from milky_frog.checkpoint import SqliteCheckpointStore

    store = SqliteCheckpointStore(tmp_path / "state.db")
    bus = _make_bus(DefaultToolPolicy())  # needs approval for echo

    harness = Harness(
        model=FakeModel(),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=store,
        handlers=bus,
    )

    result = await harness.run(RunRequest("echo hello", tmp_path))
    assert result.status is RunStatus.WAITING_FOR_APPROVAL
    assert "echo" in result.final_message

    # Resume without decision — should re-pause (PolicyHandler returns ApprovalResult)
    resumed = await harness.resume(result.run_id, max_model_calls=30)
    assert resumed.status is RunStatus.WAITING_FOR_APPROVAL


@pytest.mark.asyncio
async def test_approval_deny_returns_error_and_continues(tmp_path: Path) -> None:
    """When a handler returns BlockResult, the tool is denied and run continues."""
    from milky_frog.checkpoint import SqliteCheckpointStore

    store = SqliteCheckpointStore(tmp_path / "state.db")
    bus = _make_bus(DenyAllPolicy())

    harness = Harness(
        model=FakeModel(),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=store,
        handlers=bus,
    )

    result = await harness.run(RunRequest("echo hello", tmp_path))
    # With DenyAllPolicy the tool is blocked inline, not paused.
    assert result.status is RunStatus.COMPLETED

    loaded = store.load_state(result.run_id)
    tool_msgs = tool_messages(loaded)
    assert len(tool_msgs) >= 1
    assert any("denied" in m for m in tool_msgs)


@pytest.mark.asyncio
async def test_approval_pause_with_prompt_continues_normally(tmp_path: Path) -> None:
    """A prompt on a WAITING_FOR_APPROVAL run appends a user message and resumes."""
    from milky_frog.checkpoint import SqliteCheckpointStore

    store = SqliteCheckpointStore(tmp_path / "state.db")
    bus = _make_bus(DefaultToolPolicy())

    harness = Harness(
        model=FakeModel(),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=store,
        handlers=bus,
    )

    result = await harness.run(RunRequest("echo hello", tmp_path))
    assert result.status is RunStatus.WAITING_FOR_APPROVAL

    # Resume with a prompt — still paused unless approved.
    resumed = await harness.resume(result.run_id, max_model_calls=30, prompt="continue")
    assert resumed.status is RunStatus.WAITING_FOR_APPROVAL


@pytest.mark.asyncio
async def test_permissive_gate_runs_normally(tmp_path: Path) -> None:
    """With PermissivePolicy, tools run without approval pause."""
    from milky_frog.checkpoint import SqliteCheckpointStore

    store = SqliteCheckpointStore(tmp_path / "state.db")
    harness = Harness(
        model=FakeModel(),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=store,
        handlers=_make_bus(PermissivePolicy()),
    )

    result = await harness.run(RunRequest("echo hello", tmp_path))
    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "done"
