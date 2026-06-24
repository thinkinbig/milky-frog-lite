from __future__ import annotations

from pathlib import Path

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.domain import (
    MessageRole,
    ModelResponse,
    RunState,
    RunStatus,
    ToolCall,
)
from milky_frog.harness.state import (
    append_model_response,
    start_run,
)


def seed_run(
    store: SqliteCheckpointStore,
    run_id: str,
    workspace: Path,
    *,
    prompt: str = "go",
    status: RunStatus = RunStatus.RUNNING,
    final_message: str | None = None,
) -> RunState:
    store.create_run(run_id, workspace)
    state = start_run(RunState(run_id=run_id, workspace=workspace), prompt)
    store.save_state(run_id, state, status=status, final_message=final_message)
    return state


def seed_assistant_turn(
    store: SqliteCheckpointStore,
    run_id: str,
    workspace: Path,
    *,
    prompt: str = "go",
    content: str = "already done",
    status: RunStatus = RunStatus.RUNNING,
    final_message: str | None = None,
) -> RunState:
    state = seed_run(store, run_id, workspace, prompt=prompt, status=status)
    state = append_model_response(state, ModelResponse(content=content))
    store.save_state(run_id, state, status=status, final_message=final_message)
    return state


def seed_interrupted_tool_run(
    store: SqliteCheckpointStore,
    run_id: str,
    workspace: Path,
    *,
    prompt: str = "go",
    tool_call: ToolCall | None = None,
    status: RunStatus = RunStatus.RUNNING,
    final_message: str | None = None,
) -> RunState:
    call = tool_call or ToolCall("call-1", "echo", {"text": "hi"})
    state = seed_run(store, run_id, workspace, prompt=prompt, status=status)
    state = append_model_response(
        state,
        ModelResponse(content="", tool_calls=(call,)),
    )
    store.save_state(run_id, state, status=status, final_message=final_message)
    return state


def seed_failed_run(
    store: SqliteCheckpointStore,
    run_id: str,
    workspace: Path,
    *,
    prompt: str = "go",
    message: str = "boom",
) -> RunState:
    state = seed_run(
        store,
        run_id,
        workspace,
        prompt=prompt,
        status=RunStatus.FAILED,
        final_message=message,
    )
    return state


def user_messages(state: RunState) -> tuple[str, ...]:
    return tuple(message.content for message in state.messages if message.role is MessageRole.USER)


def tool_messages(state: RunState) -> tuple[str, ...]:
    return tuple(message.content for message in state.messages if message.role is MessageRole.TOOL)


def has_tool_result(state: RunState, tool_call_id: str) -> bool:
    return any(
        message.role is MessageRole.TOOL and message.tool_call_id == tool_call_id
        for message in state.messages
    )


def run_status(store: SqliteCheckpointStore, run_id: str) -> RunStatus:
    run = store.get_run(run_id)
    assert run is not None
    return run.status
