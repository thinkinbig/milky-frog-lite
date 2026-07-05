from __future__ import annotations

from pathlib import Path

import pytest

from milky_frog.domain import RunState, ToolCall, ToolResult
from milky_frog.events import EventHub
from milky_frog.tui.bash_render import BashRenderHandler
from milky_frog.tui.messages import BashOutputMsg
from milky_frog.tui.rendering import (
    build_diff_lines,
    complete_command,
    file_change_diff,
    format_tool_call,
    matching_commands,
    summarize_tool_result,
)


def test_format_tool_call_prefers_primary_subject() -> None:
    assert format_tool_call("read", {"file_path": "src/app.py"}) == "Read(src/app.py)"


def test_format_tool_call_prefers_pattern_over_path() -> None:
    # Search tools carry both; the pattern is the meaningful subject.
    assert format_tool_call("grep", {"pattern": "def foo", "path": "."}) == "Grep(def foo)"


def test_format_tool_call_collapses_and_truncates_long_values() -> None:
    rendered = format_tool_call("bash", {"command": "echo " + "x" * 200})
    assert rendered.startswith("Bash(echo ")
    assert rendered.endswith("…)")
    assert len(rendered) < 80


def test_format_tool_call_falls_back_to_key_value_pairs() -> None:
    rendered = format_tool_call("custom", {"a": 1, "b": 2, "c": 3})
    assert rendered == "Custom(a=1, b=2, …)"


def test_format_tool_call_without_arguments() -> None:
    assert format_tool_call("status", {}) == "Status()"


def test_summarize_tool_result_counts_extra_lines() -> None:
    assert summarize_tool_result("one\ntwo\nthree", is_error=False) == "one (+2 more lines)"


def test_summarize_tool_result_single_line() -> None:
    assert summarize_tool_result("done", is_error=False) == "done"


def test_summarize_tool_result_empty_distinguishes_error() -> None:
    assert summarize_tool_result("", is_error=False) == "(no output)"
    assert summarize_tool_result("   ", is_error=True) == "(failed)"


def test_matching_commands_filters_by_prefix() -> None:
    assert [command.name for command in matching_commands("/c")] == ["/clear"]
    assert matching_commands("/zzz") == ()


def test_matching_commands_includes_runs() -> None:
    names = {command.name for command in matching_commands("/r")}
    assert "/resume" in names
    assert "/runs" in names


def test_complete_command_resolves_unique_prefix() -> None:
    assert complete_command("/cl") == "/clear"
    assert complete_command("/help") == "/help"


def test_complete_command_stays_ambiguous_for_bare_slash() -> None:
    assert complete_command("/") is None


def test_build_diff_lines_marks_add_remove_and_context() -> None:
    rows = build_diff_lines("a\nb\nc", "a\nB\nc")
    assert ("context", "a") in rows
    assert ("remove", "b") in rows
    assert ("add", "B") in rows
    assert ("context", "c") in rows
    # hunk/file headers are dropped
    assert all(not text.startswith("@@") for _, text in rows)


def test_file_change_diff_for_edit_file() -> None:
    rows = file_change_diff("edit_file", {"path": "x.py", "old": "foo", "new": "bar"})
    assert rows is not None
    assert ("remove", "foo") in rows
    assert ("add", "bar") in rows


def test_file_change_diff_for_write_file_is_all_additions() -> None:
    rows = file_change_diff("write_file", {"path": "x.py", "content": "one\ntwo"})
    assert rows == [("add", "one"), ("add", "two")]


def test_file_change_diff_returns_none_for_other_tools() -> None:
    assert file_change_diff("read_file", {"path": "x.py"}) is None
    assert file_change_diff("edit_file", {"path": "x.py"}) is None  # missing old/new


@pytest.mark.asyncio
async def test_bash_render_handler_prefers_display_content(tmp_path: Path) -> None:
    messages: list[BashOutputMsg] = []
    hub = EventHub()
    BashRenderHandler(messages.append).register(hub)

    await hub.after_tool(
        "run-1",
        ToolCall("call-1", "bash", {"command": "printf color"}),
        ToolResult("plain", display_content="\x1b[31mcolor\x1b[0m"),
        RunState(run_id="run-1", workspace=tmp_path),
    )

    assert len(messages) == 1
    assert messages[0].content == "\x1b[31mcolor\x1b[0m"
