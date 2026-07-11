"""Tests for ``ConversationViewModel`` streaming state machine.

The VM talks to its host through the ``TuiHost`` Protocol; we stub the host
(see ``tests/tui/_fakes.py``) and inspect what gets appended/scrolled/
intervaled. No Textual spin-up — these tests verify the VM's render
decisions, not the widget tree.
"""

from __future__ import annotations

from typing import cast

from milky_frog.tui.viewmodels.conversation_vm import ConversationViewModel
from tests.tui._fakes import FakeStatic, FakeTuiHost


def _vm() -> tuple[ConversationViewModel, FakeTuiHost]:
    host = FakeTuiHost()
    return host._conv, host


# ── Thinking phase ──────────────────────────────────────────────────


def test_on_thinking_opens_phase_and_appends_widget() -> None:
    vm, host = _vm()

    vm.on_thinking("Let me think")

    assert vm.phase == "thinking"
    assert len(host.appended) == 1
    assert host.intervals == [(0.1, vm._tick_thinking_spinner)]


def test_on_thinking_updates_existing_widget_in_place() -> None:
    vm, host = _vm()

    vm.on_thinking("first")
    first_widget = vm._thinking_widget
    assert first_widget is not None

    vm.on_thinking(" second")

    assert len(host.appended) == 1  # no new widget for follow-up chunks
    assert vm._thinking_widget is first_widget


def test_close_phase_flushes_thinking_with_non_empty_buffer() -> None:
    vm, _ = _vm()

    vm.on_thinking("reasoning...")
    timer = vm._thinking_spinner_timer
    assert timer is not None

    vm.close_phase()

    assert vm.phase is None
    assert vm._thinking_buf == []
    assert vm._thinking_widget is None
    assert timer.stopped is True
    assert vm._thinking_spinner_timer is None


def test_close_phase_removes_widget_when_thinking_buffer_empty() -> None:
    """A thinking phase opened and immediately closed without text should vanish."""
    vm, _ = _vm()

    vm.on_thinking("")  # opens phase, buffers nothing
    widget = vm._thinking_widget
    assert widget is not None

    vm.close_phase()

    assert vm.phase is None
    assert widget.removed is True


def test_tick_thinking_spinner_advances_frame_and_updates_widget() -> None:
    vm, _ = _vm()
    vm.on_thinking("draft")

    vm._tick_thinking_spinner()
    assert vm._thinking_frame_idx == 1

    vm._tick_thinking_spinner()
    assert vm._thinking_frame_idx == 2

    # Cycles back to zero after ``len(spinner_frames)`` ticks.
    for _ in range(len(vm._SPINNER_FRAMES) - 2):
        vm._tick_thinking_spinner()
    assert vm._thinking_frame_idx == 0


# ── Answer phase ────────────────────────────────────────────────────


def test_on_text_opens_answer_phase() -> None:
    vm, host = _vm()

    vm.on_text("hello world")

    assert vm.phase == "answer"
    assert len(host.appended) == 1


def test_on_text_updates_existing_answer_widget_in_place() -> None:
    vm, host = _vm()

    vm.on_text("chunk-1")
    widget = vm._answer_widget
    assert widget is not None

    vm.on_text("chunk-2")

    assert len(host.appended) == 1
    assert vm._answer_widget is widget


def test_commit_answer_clears_buffer_and_releases_widget() -> None:
    """Closing the answer phase clears the buffer; the widget stays attached."""
    vm, _ = _vm()

    vm.on_text("hello")
    assert vm._answer_widget is not None

    vm.close_phase()

    assert vm.phase is None
    assert vm._answer_buf == []
    assert vm._answer_widget is None


# ── Phase transitions ──────────────────────────────────────────────


def test_answer_then_thinking_closes_answer_first() -> None:
    vm, host = _vm()

    vm.on_text("answering")
    assert vm._answer_widget is not None

    vm.on_thinking("switched to reasoning")

    # Answer widget released; thinking widget appended.
    assert vm._answer_widget is None
    assert vm.phase == "thinking"
    assert len(host.appended) == 2


def test_close_phase_with_no_open_phase_is_noop() -> None:
    vm, host = _vm()

    vm.close_phase()  # never opened anything

    assert vm.phase is None
    assert host.appended == []


# ── Tool call lifecycle ────────────────────────────────────────────


def test_on_tool_call_appends_widget_and_starts_spinner() -> None:
    vm, host = _vm()

    vm.on_tool_call("call-1", "bash", {"command": "ls"})

    assert len(host.appended) == 1  # signature row only (bash has no diff)
    assert vm._active_tool_widgets["call-1"] is not None
    assert vm._active_tool_signatures["call-1"].startswith("Bash")
    assert any(interval == 0.1 for interval, _ in host.intervals)


def test_on_tool_call_for_edit_appends_signature_plus_diff() -> None:
    vm, host = _vm()

    vm.on_tool_call("call-1", "edit_file", {"path": "a.py", "old": "foo", "new": "bar"})

    # signature row + diff row, both unspaced.
    assert len(host.appended) == 2
    assert all(spaced is False for _, spaced in host.appended)


def test_finalize_tool_call_updates_widget_and_stops_spinner() -> None:
    vm, _ = _vm()
    vm.on_tool_call("call-1", "bash", {"command": "ls"})
    timer = vm._tool_spinner_timer
    assert timer is not None

    vm.finalize_tool_call("call-1", is_error=False)

    assert timer.stopped is True
    assert vm._tool_spinner_timer is None
    assert vm._active_tool_widgets == {}


def test_concurrent_tool_calls_animate_and_finalize_independently() -> None:
    vm, _ = _vm()
    vm.on_tool_call("call-1", "subagent", {"prompt": "first"})
    vm.on_tool_call("call-2", "subagent", {"prompt": "second"})
    first = cast(FakeStatic, vm._active_tool_widgets["call-1"])
    second = cast(FakeStatic, vm._active_tool_widgets["call-2"])
    timer = vm._tool_spinner_timer

    vm._tick_tool_spinner()

    assert first.updates
    assert second.updates
    vm.finalize_tool_call("call-1", is_error=False)
    assert timer is not None
    assert timer.stopped is False
    assert "call-2" in vm._active_tool_widgets

    vm.finalize_tool_call("call-2", is_error=False)
    assert timer.stopped is True
    assert vm._tool_spinner_timer is None


def test_finish_resets_thinking_answer_and_tool_state() -> None:
    vm, _ = _vm()

    vm.on_thinking("reasoning...")
    vm.on_tool_call("call-1", "bash", {"command": "ls"})
    tool_timer = vm._tool_spinner_timer
    assert tool_timer is not None

    vm.finish()

    assert vm.phase is None  # close_phase() ran
    assert tool_timer.stopped is True
    assert vm._tool_spinner_timer is None
    assert vm._active_tool_widgets == {}


# ── Static renderers ───────────────────────────────────────────────


def test_render_user_appends_user_row() -> None:
    vm, host = _vm()

    vm.render_user("hi there")

    assert len(host.appended) == 1
    assert host.appended[0][1] is True  # spaced by default


def test_render_error_with_hint_appends_error_then_hint() -> None:
    vm, host = _vm()

    vm.render_error("boom", hint="try restarting")

    assert len(host.appended) == 2
    assert host.appended[0][1] is False  # error row: spaced=False when hint provided
    assert host.appended[1][1] is True  # hint row: spaced=True (default)


def test_render_error_without_hint_uses_spaced_true() -> None:
    vm, host = _vm()

    vm.render_error("boom")

    assert len(host.appended) == 1
    assert host.appended[0][1] is True


def test_render_notification_appends_a_row() -> None:
    vm, host = _vm()

    vm.render_notification("careful", level="warning")

    assert len(host.appended) == 1


def test_render_command_output_appends_renderable() -> None:
    vm, host = _vm()

    vm.render_command_output("ok", is_error=False)

    assert len(host.appended) >= 1
    assert host.appended[-1][1] is False  # render_command_output uses spaced=False


def test_render_tool_result_finalizes_active_call_then_appends_block() -> None:
    vm, _ = _vm()
    vm.on_tool_call("call-1", "bash", {"command": "ls"})

    vm.render_tool_result("call-1", "bash", "file.py", is_error=False)

    assert vm._active_tool_widgets == {}
    assert vm._tool_spinner_timer is None


# ── Compaction ──────────────────────────────────────────────────────


def test_start_compaction_appends_animated_spinner() -> None:
    vm, host = _vm()

    vm.start_compaction()

    assert vm._compaction_widget is not None
    assert (0.1, vm._tick_compaction_spinner) in host.intervals


def test_finish_compaction_settles_spinner_in_place() -> None:
    vm, _ = _vm()
    vm.start_compaction()
    widget = vm._compaction_widget
    timer = vm._compaction_spinner_timer
    assert widget is not None

    vm.finish_compaction(7)

    assert vm._compaction_widget is None
    assert timer is not None and timer.stopped
    rendered = widget.updates[-1].plain  # type: ignore[attr-defined]
    assert "Compacted" in rendered
    assert "7 messages" in rendered


def test_finish_compaction_without_spinner_appends_line() -> None:
    # The automatic path never starts a spinner; finish appends directly.
    vm, host = _vm()

    vm.finish_compaction(1)

    assert len(host.appended) == 1
    rendered = host.appended[-1][0].plain
    assert "1 message" in rendered  # singular noun for one message


def test_cancel_compaction_shows_reason() -> None:
    vm, _ = _vm()
    vm.start_compaction()
    widget = vm._compaction_widget
    assert widget is not None

    vm.cancel_compaction("nothing to summarise")

    assert vm._compaction_spinner_timer is None
    assert "nothing to summarise" in widget.updates[-1].plain  # type: ignore[attr-defined]
