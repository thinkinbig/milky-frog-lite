from __future__ import annotations

from pathlib import Path

import pytest

from milky_frog.adapters.local import LocalSandbox
from milky_frog.domain import RunState, ToolCall, ToolResult, VerificationNotice
from milky_frog.events.events import RunAfterTool
from milky_frog.events.hub import EventHub
from milky_frog.handlers.verification import VerificationHandler
from milky_frog.project import (
    CONFIG_FILENAME,
    PROJECT_DIRNAME,
)


def _write_config(workspace: Path, body: str) -> None:
    root = workspace / PROJECT_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    (root / CONFIG_FILENAME).write_text(body, encoding="utf-8")


def _make_state(workspace: Path) -> RunState:
    """Stub RunState with just the fields VerificationHandler reads."""
    return RunState(run_id="test", workspace=workspace)


@pytest.mark.asyncio
async def test_triggers_on_edit_file_success(tmp_path: Path) -> None:
    """Handler returns VerificationNotice when edit_file succeeds."""
    handler = VerificationHandler(LocalSandbox)
    hub = EventHub()
    handler.register(hub)

    state = _make_state(tmp_path)
    event = RunAfterTool(
        run_id="test",
        call=ToolCall("call-1", "edit_file", {"path": "foo.py", "old": "x", "new": "y"}),
        result=ToolResult("ok", is_error=False),
        state=state,
    )

    results = await hub.after_tool("test", event.call, event.result, event.state)
    notices = [r for r in results if isinstance(r, VerificationNotice)]
    assert len(notices) == 1
    assert "uv run ruff check" in notices[0].summary
    assert "uv run pytest" in notices[0].summary


@pytest.mark.asyncio
async def test_triggers_on_write_file_success(tmp_path: Path) -> None:
    handler = VerificationHandler(LocalSandbox)
    hub = EventHub()
    handler.register(hub)

    state = _make_state(tmp_path)
    event = RunAfterTool(
        run_id="test",
        call=ToolCall("call-1", "write_file", {"path": "foo.py", "content": "x=1"}),
        result=ToolResult("ok", is_error=False),
        state=state,
    )

    results = await hub.after_tool("test", event.call, event.result, event.state)
    assert any(isinstance(r, VerificationNotice) for r in results)


@pytest.mark.asyncio
async def test_no_trigger_on_non_edit_tool(tmp_path: Path) -> None:
    handler = VerificationHandler(LocalSandbox)
    hub = EventHub()
    handler.register(hub)

    state = _make_state(tmp_path)
    for tool_name in ("read_file", "grep", "list_dir", "bash", "fetch"):
        event = RunAfterTool(
            run_id="test",
            call=ToolCall("call-1", tool_name, {}),
            result=ToolResult("ok", is_error=False),
            state=state,
        )
        results = await hub.after_tool("test", event.call, event.result, event.state)
        assert not any(isinstance(r, VerificationNotice) for r in results)


@pytest.mark.asyncio
async def test_no_trigger_on_edit_error(tmp_path: Path) -> None:
    handler = VerificationHandler(LocalSandbox)
    hub = EventHub()
    handler.register(hub)

    state = _make_state(tmp_path)
    event = RunAfterTool(
        run_id="test",
        call=ToolCall("call-1", "edit_file", {"path": "foo.py", "old": "x", "new": "y"}),
        result=ToolResult("not found", is_error=True),
        state=state,
    )

    results = await hub.after_tool("test", event.call, event.result, event.state)
    assert not any(isinstance(r, VerificationNotice) for r in results)


@pytest.mark.asyncio
async def test_disabled_when_after_edit_false(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "[verification]\nafter_edit = false\n",
    )

    handler = VerificationHandler(LocalSandbox)
    hub = EventHub()
    handler.register(hub)

    state = _make_state(tmp_path)
    event = RunAfterTool(
        run_id="test",
        call=ToolCall("call-1", "edit_file", {"path": "x", "old": "a", "new": "b"}),
        result=ToolResult("ok", is_error=False),
        state=state,
    )

    results = await hub.after_tool("test", event.call, event.result, event.state)
    assert not any(isinstance(r, VerificationNotice) for r in results)


@pytest.mark.asyncio
async def test_verification_uses_sandbox_run_command(tmp_path: Path) -> None:
    """Commands go through the Sandbox seam, not a raw host subprocess."""
    from tests.stubs import RecordingCommandSandboxFactory

    _write_config(tmp_path, '[verification]\nafter_edit = true\ncommands = ["echo hello"]\n')
    recorder = RecordingCommandSandboxFactory()
    handler = VerificationHandler(recorder)
    hub = EventHub()
    handler.register(hub)

    state = _make_state(tmp_path)
    call = ToolCall("call-1", "edit_file", {"path": "foo.py", "old": "x", "new": "y"})
    results = await hub.after_tool("test", call, ToolResult("ok", is_error=False), state)

    notices = [r for r in results if isinstance(r, VerificationNotice)]
    assert recorder.commands == ["echo hello"]
    assert len(notices) == 1
    assert notices[0].exit_code_summary == "all pass"


@pytest.mark.asyncio
async def test_verification_reports_timeout_as_failure(tmp_path: Path) -> None:
    """Timeout is reported as failure, not raised."""
    from tests.stubs import TimingOutSandboxFactory

    _write_config(tmp_path, '[verification]\nafter_edit = true\ncommands = ["sleep 99"]\n')
    handler = VerificationHandler(TimingOutSandboxFactory())
    hub = EventHub()
    handler.register(hub)

    state = _make_state(tmp_path)
    call = ToolCall("call-1", "edit_file", {"path": "foo.py", "old": "x", "new": "y"})
    results = await hub.after_tool("test", call, ToolResult("ok", is_error=False), state)

    notices = [r for r in results if isinstance(r, VerificationNotice)]
    assert len(notices) == 1
    assert notices[0].exit_code_summary == "one or more commands FAILED"
    assert "timed out" in notices[0].summary
