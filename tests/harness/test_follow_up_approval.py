"""A Tool's ``follow_up`` deterministically pauses the Run for approval.

Models the ``subagent`` → ``merge_worktree`` case with cheap stubs: a Tool
whose outcome always carries a follow-up, and a Tool standing in for
``merge_worktree`` that always needs approval. The point under test is that
this pause is driven by the harness/policy, not by the model choosing to
raise it in its next turn.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic import BaseModel

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.domain import (
    ApprovalDecision,
    ApprovalVerdict,
    FollowUpCall,
    ModelChunk,
    ModelRequest,
    ModelResponse,
    RunRequest,
    RunState,
    RunStatus,
    StreamDone,
    ToolCall,
)
from milky_frog.events import EventHub
from milky_frog.events.emitter import format_approval_message
from milky_frog.harness.state import (
    append_model_response,
    append_synthetic_tool_call,
    append_tool_result,
    unmatched_tool_calls,
)
from milky_frog.harness.tools import ToolContext, ToolRegistry, ToolResult
from tests.stubs import make_harness


class LeavesFollowUpInput(BaseModel):
    pass


class LeavesFollowUpTool:
    """Stub tool whose outcome always requests a ``confirm`` follow-up."""

    name = "leaves_followup"
    requires_approval = False
    description = "Leaves a pending decision behind"
    input_model: type[BaseModel] = LeavesFollowUpInput

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        del context, input
        return ToolResult(
            "left something behind",
            follow_up=FollowUpCall(tool_name="confirm", arguments={"thing": "worktree"}),
        )


class ConfirmInput(BaseModel):
    thing: str


class ConfirmTool:
    """Stub standing in for ``merge_worktree``: always needs approval."""

    name = "confirm"
    requires_approval = True
    description = "Confirms the pending decision"
    input_model: type[BaseModel] = ConfirmInput

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        del context
        parsed = ConfirmInput.model_validate(input)
        self.calls.append(parsed.thing)
        return ToolResult(f"confirmed {parsed.thing}")


class _CallsThenFinishesModel:
    """Requests ``leaves_followup`` once, then finishes once resumed."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        self.calls += 1
        if self.calls == 1:
            yield StreamDone(ModelResponse(tool_calls=(ToolCall("call-1", "leaves_followup", {}),)))
            return
        yield StreamDone(ModelResponse(content="done"))


def _make_test_harness(store: SqliteCheckpointStore) -> tuple[object, ConfirmTool]:
    confirm = ConfirmTool()
    harness = make_harness(
        model=_CallsThenFinishesModel(),
        tools=ToolRegistry((LeavesFollowUpTool(), confirm)),
        checkpoints=store,
        hub=EventHub(),
    )
    # make_harness() defaults to auto_approve(); the override wins over that
    # mode regardless, so "confirm" always pauses like merge_worktree would.
    harness.policy.require_approval("confirm")
    return harness, confirm


@pytest.mark.asyncio
async def test_tool_follow_up_pauses_run_for_approval(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    harness, confirm = _make_test_harness(store)

    result = await harness.run(RunRequest("do the thing", tmp_path, max_model_calls=3))

    assert result.status is RunStatus.WAITING_FOR_APPROVAL
    state = store.load_state(result.run_id)
    pending = unmatched_tool_calls(state.messages)
    assert [call.name for call in pending] == ["confirm"]
    assert pending[0].arguments == {"thing": "worktree"}
    assert confirm.calls == []  # not executed yet — still waiting on the human


@pytest.mark.asyncio
async def test_approving_follow_up_executes_it_and_run_completes(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    harness, confirm = _make_test_harness(store)

    paused = await harness.run(RunRequest("do the thing", tmp_path, max_model_calls=3))

    result = await harness.respond_approval(
        paused.run_id,
        max_model_calls=3,
        approval=ApprovalVerdict(ApprovalDecision.APPROVE),
    )

    assert result.status is RunStatus.COMPLETED
    assert confirm.calls == ["worktree"]


@pytest.mark.asyncio
async def test_follow_up_allowed_by_override_executes_without_pausing(tmp_path: Path) -> None:
    """If a policy override resolves the synthesized call to ALLOW (not the
    default NEEDS_APPROVAL), the loop must still execute it inline instead of
    leaving it dangling unmatched in the transcript."""
    store = SqliteCheckpointStore(tmp_path / "state.db")
    confirm = ConfirmTool()
    harness = make_harness(
        model=_CallsThenFinishesModel(),
        tools=ToolRegistry((LeavesFollowUpTool(), confirm)),
        checkpoints=store,
        hub=EventHub(),
    )
    # make_harness() already puts the policy in auto_approve mode; this
    # confirms that mode (not just an explicit allow() override) is honored
    # for a harness-synthesized call the same as for a model-issued one.

    result = await harness.run(RunRequest("do the thing", tmp_path, max_model_calls=3))

    assert result.status is RunStatus.COMPLETED
    assert confirm.calls == ["worktree"]
    state = store.load_state(result.run_id)
    assert unmatched_tool_calls(state.messages) == ()


@pytest.mark.asyncio
async def test_denying_follow_up_never_executes_it(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    harness, confirm = _make_test_harness(store)

    paused = await harness.run(RunRequest("do the thing", tmp_path, max_model_calls=3))

    result = await harness.respond_approval(
        paused.run_id,
        max_model_calls=3,
        approval=ApprovalVerdict(ApprovalDecision.DENY, denial_reason="not now"),
    )

    assert result.status is RunStatus.COMPLETED
    assert confirm.calls == []


def test_synthetic_call_does_not_hide_a_sibling_awaiting_approval() -> None:
    """A follow-up lands in its own assistant message; siblings must survive it.

    ``unmatched_tool_calls`` scanning only the trailing assistant message would
    drop ``call-bash`` here, so its verdict could never be applied and the
    transcript would keep an assistant ``tool_calls`` entry with no Tool result
    — which providers reject on the next turn.
    """
    state = RunState("run-1", Path("/tmp"))
    state = append_model_response(
        state,
        ModelResponse(
            content="",
            tool_calls=(
                ToolCall("call-subagent", "subagent", {"capability": "write"}),
                ToolCall("call-bash", "bash", {"command": "ls"}),
            ),
        ),
    )
    state = append_tool_result(state, ToolCall("call-subagent", "subagent", {}), ToolResult("done"))
    state = append_synthetic_tool_call(
        state, ToolCall("call-subagent:follow-up", "merge_worktree", {})
    )

    assert [call.id for call in unmatched_tool_calls(state.messages)] == [
        "call-bash",
        "call-subagent:follow-up",
    ]


def test_two_synthetic_calls_in_one_batch_both_stay_pending() -> None:
    """Two write subagents each leaving a worktree must both get a merge decision."""
    state = RunState("run-2", Path("/tmp"))
    state = append_model_response(
        state,
        ModelResponse(
            content="",
            tool_calls=(ToolCall("a", "subagent", {}), ToolCall("b", "subagent", {})),
        ),
    )
    state = append_tool_result(state, ToolCall("a", "subagent", {}), ToolResult("done"))
    state = append_tool_result(state, ToolCall("b", "subagent", {}), ToolResult("done"))
    state = append_synthetic_tool_call(state, ToolCall("a:follow-up", "merge_worktree", {}))
    state = append_synthetic_tool_call(state, ToolCall("b:follow-up", "merge_worktree", {}))

    assert [call.id for call in unmatched_tool_calls(state.messages)] == [
        "a:follow-up",
        "b:follow-up",
    ]


def test_long_prompt_is_truncated_in_the_approval_preview() -> None:
    """A multi-KB subagent prompt must not push the approval menu off screen."""
    message = format_approval_message(
        ToolCall("c1", "subagent", {"prompt": "x" * 5000, "capability": "write"})
    )

    assert len(message) < 400
    assert "…" in message
