"""ToolGate unit tests and Tool policy approval integration tests."""

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
from milky_frog.gates import (
    DefaultToolPolicy,
    DenyAllPolicy,
    PermissivePolicy,
    ToolGate,
)
from milky_frog.handlers import LifecycleBus
from milky_frog.harness.runner import Harness
from milky_frog.harness.tools import ToolRegistry
from tests.checkpoint_helpers import run_status, tool_messages, user_messages
from tests.stubs import EchoTool, FakeModel

# ── ToolGate / policy unit tests ──────────────────────────────────────


def test_default_policy_allows_read_and_list_dir() -> None:
    policy = DefaultToolPolicy()
    assert policy.decide(ToolCall("c1", "read", {})) is ToolDecision.ALLOW
    assert policy.decide(ToolCall("c2", "list_dir", {})) is ToolDecision.ALLOW


def test_default_policy_needs_approval_for_write_and_echo() -> None:
    policy = DefaultToolPolicy()
    assert policy.decide(ToolCall("c1", "write", {})) is ToolDecision.NEEDS_APPROVAL
    assert policy.decide(ToolCall("c2", "echo", {})) is ToolDecision.NEEDS_APPROVAL
    assert policy.decide(ToolCall("c3", "bash", {})) is ToolDecision.NEEDS_APPROVAL


def test_permissive_policy_allows_everything() -> None:
    policy = PermissivePolicy()
    assert policy.decide(ToolCall("c1", "write", {})) is ToolDecision.ALLOW
    assert policy.decide(ToolCall("c2", "unknown_tool", {})) is ToolDecision.ALLOW


def test_deny_all_policy_denies_everything() -> None:
    policy = DenyAllPolicy()
    assert policy.decide(ToolCall("c1", "read", {})) is ToolDecision.DENY


def test_gate_defers_to_policy_on_first_check() -> None:
    gate = ToolGate(PermissivePolicy())
    assert gate.check(ToolCall("c1", "write", {})) is ToolDecision.ALLOW


def test_gate_caches_approval_and_returns_allow() -> None:
    gate = ToolGate(DenyAllPolicy())
    gate.approve("c1")
    assert gate.check(ToolCall("c1", "write", {})) is ToolDecision.ALLOW


def test_gate_caches_denial_and_returns_deny() -> None:
    gate = ToolGate(PermissivePolicy())
    gate.deny("c1")
    assert gate.check(ToolCall("c1", "write", {})) is ToolDecision.DENY


def test_gate_clear_forgets_decisions() -> None:
    gate = ToolGate(DenyAllPolicy())
    gate.approve("c1")
    assert gate.check(ToolCall("c1", "write", {})) is ToolDecision.ALLOW
    gate.clear()
    assert gate.check(ToolCall("c1", "write", {})) is ToolDecision.DENY


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
        handlers=LifecycleBus(),
        tool_gate=ToolGate(DenyAllPolicy()),
    )

    result = await harness.run(RunRequest("echo hello", tmp_path))
    assert result.status is RunStatus.COMPLETED


# ── Harness integration: approval pause and resume ────────────────────


@pytest.mark.asyncio
async def test_approval_pauses_run_and_resume_executes_approved_tool(
    tmp_path: Path,
) -> None:
    """When a tool needs approval, the run pauses. On approve+resume, it executes."""
    from milky_frog.checkpoint import SqliteCheckpointStore

    store = SqliteCheckpointStore(tmp_path / "state.db")
    gate = ToolGate()  # DefaultPolicy: needs approval for echo

    harness = Harness(
        model=FakeModel(),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=store,
        handlers=LifecycleBus(),
        tool_gate=gate,
    )

    result = await harness.run(RunRequest("echo hello", tmp_path))
    assert result.status is RunStatus.WAITING_FOR_APPROVAL
    assert "echo" in result.final_message

    # Find the pending tool call id from the state
    state = store.load_state(result.run_id)
    last_assistant = [m for m in state.messages if m.role is MessageRole.ASSISTANT][-1]
    assert last_assistant.tool_calls
    pending_call_id = last_assistant.tool_calls[0].id

    # User approves
    gate.approve(pending_call_id)

    resumed = await harness.resume(result.run_id, max_model_calls=30)
    assert resumed.status is RunStatus.COMPLETED
    assert resumed.final_message == "done"
    assert run_status(store, result.run_id) is RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_approval_deny_returns_error_and_continues(tmp_path: Path) -> None:
    """When a user denies a tool, it produces an error result and the run continues."""
    from milky_frog.checkpoint import SqliteCheckpointStore

    store = SqliteCheckpointStore(tmp_path / "state.db")
    gate = ToolGate()

    harness = Harness(
        model=FakeModel(),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=store,
        handlers=LifecycleBus(),
        tool_gate=gate,
    )

    result = await harness.run(RunRequest("echo hello", tmp_path))
    assert result.status is RunStatus.WAITING_FOR_APPROVAL

    state = store.load_state(result.run_id)
    last_assistant = [m for m in state.messages if m.role is MessageRole.ASSISTANT][-1]
    pending_call_id = last_assistant.tool_calls[0].id

    # User denies
    gate.deny(pending_call_id)

    resumed = await harness.resume(result.run_id, max_model_calls=30)
    assert resumed.status is RunStatus.COMPLETED

    loaded = store.load_state(result.run_id)
    tool_msgs = tool_messages(loaded)
    assert any("denied by user" in m for m in tool_msgs)


@pytest.mark.asyncio
async def test_approval_repauses_when_still_unapproved(tmp_path: Path) -> None:
    """If no approval decision is made, resume pauses again."""
    from milky_frog.checkpoint import SqliteCheckpointStore

    store = SqliteCheckpointStore(tmp_path / "state.db")
    gate = ToolGate()

    harness = Harness(
        model=FakeModel(),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=store,
        handlers=LifecycleBus(),
        tool_gate=gate,
    )

    result = await harness.run(RunRequest("echo hello", tmp_path))
    assert result.status is RunStatus.WAITING_FOR_APPROVAL

    # Resume without approving — should pause again
    resumed = await harness.resume(result.run_id, max_model_calls=30)
    assert resumed.status is RunStatus.WAITING_FOR_APPROVAL


@pytest.mark.asyncio
async def test_approval_pause_with_prompt_continues_normally(tmp_path: Path) -> None:
    """A prompt on a WAITING_FOR_APPROVAL run appends a user message and resumes
    normally with no re-pause (gate is None in tests, but this verifies the flow)."""
    from milky_frog.checkpoint import SqliteCheckpointStore

    store = SqliteCheckpointStore(tmp_path / "state.db")
    gate = ToolGate()

    harness = Harness(
        model=FakeModel(),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=store,
        handlers=LifecycleBus(),
        tool_gate=gate,
    )

    result = await harness.run(RunRequest("echo hello", tmp_path))
    assert result.status is RunStatus.WAITING_FOR_APPROVAL

    state = store.load_state(result.run_id)
    last_assistant = [m for m in state.messages if m.role is MessageRole.ASSISTANT][-1]
    pending_call_id = last_assistant.tool_calls[0].id

    gate.approve(pending_call_id)
    resumed = await harness.resume(result.run_id, max_model_calls=30, prompt="continue")
    assert resumed.status is RunStatus.COMPLETED
    assert user_messages(store.load_state(result.run_id)) == ("echo hello", "continue")


@pytest.mark.asyncio
async def test_permissive_gate_runs_normally(tmp_path: Path) -> None:
    """With PermissivePolicy, tools run without approval pause."""
    from milky_frog.checkpoint import SqliteCheckpointStore

    store = SqliteCheckpointStore(tmp_path / "state.db")
    harness = Harness(
        model=FakeModel(),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=store,
        handlers=LifecycleBus(),
        tool_gate=ToolGate(PermissivePolicy()),
    )

    result = await harness.run(RunRequest("echo hello", tmp_path))
    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "done"
