from __future__ import annotations

from milky_frog.ui.tui.rendering import (
    complete_command,
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


def test_complete_command_resolves_unique_prefix() -> None:
    assert complete_command("/cl") == "/clear"
    assert complete_command("/help") == "/help"


def test_complete_command_stays_ambiguous_for_bare_slash() -> None:
    assert complete_command("/") is None
