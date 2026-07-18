"""Batch approval: per-call verdicts, concurrent execution of the approved subset."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic import BaseModel

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.domain import (
    ApprovalDecision,
    ApprovalVerdict,
    ModelChunk,
    ModelRequest,
    ModelResponse,
    RunStatus,
    StreamDone,
    ToolCall,
)
from milky_frog.events import EventHub
from milky_frog.events.events import RunAfterTool, RunBeforeTool
from milky_frog.harness.state import append_model_response, unmatched_tool_calls
from milky_frog.harness.tools import ToolContext, ToolRegistry, ToolResult
from tests.checkpoint_helpers import seed_run, tool_messages
from tests.stubs import EchoTool, make_harness


class _CompletesNextTurnModel:
    """Returns a plain completion on the turn after pending approvals resolve."""

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        yield StreamDone(ModelResponse(content="done"))


class DelayedToolInput(BaseModel):
    label: str
    delay: float = 0.0


class DelayedTool:
    name = "delayed"
    description = "Sleeps then echoes label"
    input_model: type[BaseModel] = DelayedToolInput

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        del context
        parsed = DelayedToolInput.model_validate(input)
        await asyncio.sleep(parsed.delay)
        return ToolResult(parsed.label)


def _seed_pending(
    store: SqliteCheckpointStore, run_id: str, workspace: Path, calls: tuple[ToolCall, ...]
) -> None:
    state = seed_run(store, run_id, workspace, status=RunStatus.WAITING_FOR_APPROVAL)
    state = append_model_response(state, ModelResponse(content="", tool_calls=calls))
    store.save_state(run_id, state, status=RunStatus.WAITING_FOR_APPROVAL, final_message="pending")


@pytest.mark.asyncio
async def test_respond_approvals_denies_and_approves_independently(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    run_id = "run-1"
    calls = (
        ToolCall("call-1", "echo", {"text": "keep"}),
        ToolCall("call-2", "echo", {"text": "drop"}),
    )
    _seed_pending(store, run_id, tmp_path, calls)

    harness = make_harness(
        model=_CompletesNextTurnModel(),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=store,
        hub=EventHub(),
    )

    result = await harness.respond_approvals(
        run_id,
        max_model_calls=2,
        verdicts={
            "call-1": ApprovalVerdict(ApprovalDecision.APPROVE),
            "call-2": ApprovalVerdict(ApprovalDecision.DENY, denial_reason="not needed"),
        },
    )

    assert result.run_id == run_id
    state = store.load_state(run_id)
    messages = tool_messages(state)
    assert messages[0] == "keep"
    assert "denied by user" in messages[1]
    assert "not needed" in messages[1]


@pytest.mark.asyncio
async def test_respond_approvals_runs_approved_subset_concurrently(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    run_id = "run-1"
    calls = tuple(
        ToolCall(f"call-{i}", "delayed", {"label": f"t{i}", "delay": 0.2}) for i in range(3)
    )
    _seed_pending(store, run_id, tmp_path, calls)

    harness = make_harness(
        model=_CompletesNextTurnModel(),
        tools=ToolRegistry((DelayedTool(),)),
        checkpoints=store,
        hub=EventHub(),
    )
    verdicts = {call.id: ApprovalVerdict(ApprovalDecision.APPROVE) for call in calls}

    start = asyncio.get_event_loop().time()
    result = await harness.respond_approvals(run_id, max_model_calls=2, verdicts=verdicts)
    elapsed = asyncio.get_event_loop().time() - start

    assert result.status is RunStatus.COMPLETED
    assert elapsed < 0.35  # sequential would be >= 0.6s


@pytest.mark.asyncio
async def test_respond_approvals_partial_verdicts_rehalts_on_undecided(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    run_id = "run-1"
    calls = (
        ToolCall("call-1", "echo", {"text": "a"}),
        ToolCall("call-2", "echo", {"text": "b"}),
        ToolCall("call-3", "echo", {"text": "c"}),
    )
    _seed_pending(store, run_id, tmp_path, calls)

    harness = make_harness(
        model=_CompletesNextTurnModel(),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=store,
        hub=EventHub(),
    )

    result = await harness.respond_approvals(
        run_id,
        max_model_calls=2,
        verdicts={"call-1": ApprovalVerdict(ApprovalDecision.APPROVE)},
    )

    assert result.status is RunStatus.WAITING_FOR_APPROVAL
    state = store.load_state(run_id)
    assert tool_messages(state) == ("a",)
    still_pending = unmatched_tool_calls(state.messages)
    assert {call.id for call in still_pending} == {"call-2", "call-3"}


@pytest.mark.asyncio
async def test_respond_approval_applies_verdict_only_to_first_call(tmp_path: Path) -> None:
    """The singular `respond_approval` applies only to the first pending call
    — remaining calls stay pending so the Run re-halts."""
    store = SqliteCheckpointStore(tmp_path / "state.db")
    run_id = "run-1"
    calls = (
        ToolCall("call-1", "echo", {"text": "a"}),
        ToolCall("call-2", "echo", {"text": "b"}),
    )
    _seed_pending(store, run_id, tmp_path, calls)

    harness = make_harness(
        model=_CompletesNextTurnModel(),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=store,
        hub=EventHub(),
    )

    result = await harness.respond_approval(
        run_id, max_model_calls=2, approval=ApprovalVerdict(ApprovalDecision.APPROVE)
    )

    assert result.status is RunStatus.WAITING_FOR_APPROVAL
    state = store.load_state(run_id)
    assert tool_messages(state) == ("a",)  # only the first call was executed
    still_pending = unmatched_tool_calls(state.messages)
    assert {call.id for call in still_pending} == {"call-2"}


@pytest.mark.asyncio
async def test_respond_approvals_opens_every_decided_call_including_denials(
    tmp_path: Path,
) -> None:
    """``before_tool`` fires for denials too, so no ``after_tool`` is an orphan.

    A denial still produces a ``ToolResult`` and an ``after_tool``. Subscribers
    pair the two — the TUI opens a tool card on ``before_tool`` and completes it
    on ``after_tool`` — so a result whose opener never fired renders as a bare
    "denied by user" with no indication of which call was denied.
    """
    store = SqliteCheckpointStore(tmp_path / "state.db")
    run_id = "run-1"
    calls = (
        ToolCall("call-1", "echo", {"text": "keep"}),
        ToolCall("call-2", "echo", {"text": "drop"}),
    )
    _seed_pending(store, run_id, tmp_path, calls)

    hub = EventHub()
    opened: list[str] = []
    closed: list[str] = []

    @hub.on(RunBeforeTool)
    async def _record_open(event: RunBeforeTool, _ctx: object = None) -> None:
        opened.append(event.call.id)

    @hub.on(RunAfterTool)
    async def _record_close(event: RunAfterTool, _ctx: object = None) -> None:
        closed.append(event.call.id)

    harness = make_harness(
        model=_CompletesNextTurnModel(),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=store,
        hub=hub,
    )

    await harness.respond_approvals(
        run_id,
        max_model_calls=2,
        verdicts={
            "call-1": ApprovalVerdict(ApprovalDecision.APPROVE),
            "call-2": ApprovalVerdict(ApprovalDecision.DENY),
        },
    )

    assert opened == ["call-1", "call-2"]
    assert closed == opened
