from __future__ import annotations

from collections.abc import Callable

from pydantic import JsonValue
from rich.console import RenderableType
from rich.markdown import Markdown
from rich.text import Text
from textual.timer import Timer
from textual.widgets import Static

from milky_frog.domain import RunUsage
from milky_frog.tui.render_helpers import (
    assistant_footer,
    command_summary,
    conversation_row,
    diff_renderable,
    format_tool_signature,
    thinking_block,
    tool_call_completed,
    tool_call_diff,
    tool_call_widget,
    tool_result_block,
    user_row,
)
from milky_frog.tui.render_helpers import (
    render_command_output as _render_command_output,
)
from milky_frog.tui.viewmodels.protocols import TuiHost


class ConversationViewModel:
    """Manages streaming conversation rendering state and widget lifecycle.

    Owns the mutable render buffers, spinner timers, and phase tracking.
    The App delegates to this for all message-render callbacks.
    """

    _SPINNER_FRAMES: tuple[str, ...] = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def __init__(self, app: TuiHost) -> None:
        # We only call back through a narrow protocol, not the full App.
        self._app = app

        # Streaming buffers
        self._thinking_buf: list[str] = []
        self._answer_buf: list[str] = []
        self.phase: str | None = None  # None | "thinking" | "answer"

        # Live widgets updated in-place during streaming
        self._thinking_widget: Static | None = None
        self._answer_widget: Static | None = None

        # Spinner state
        self._thinking_spinner_timer: Timer | None = None
        self._thinking_frame_idx: int = 0

        # Tool call state
        self._active_tool_widget: Static | None = None
        self._active_tool_signature: str = ""
        self._tool_spinner_timer: Timer | None = None
        self._tool_frame_idx: int = 0

    # ── Delegates to App ───────────────────────────────────────────

    def _append(self, renderable: RenderableType, *, spaced: bool = True) -> Static:
        return self._app._append(renderable, spaced=spaced)

    def _scroll_end(self) -> None:
        self._app._scroll_end()

    def _set_interval(self, interval: float, callback: Callable[[], object]) -> Timer:
        return self._app.set_interval(interval, callback)

    # ── Phase management ──────────────────────────────────────────

    def close_phase(self) -> None:
        """Close the current streaming phase, committing any buffered content."""
        if self.phase == "thinking":
            self._flush_thinking()
        elif self.phase == "answer":
            self._commit_answer()
        self.phase = None

    # ── Thinking (reasoning) ──────────────────────────────────────

    def on_thinking(self, text: str) -> None:
        """Accumulate reasoning chunks; update the live reasoning widget in place."""
        if self.phase != "thinking":
            self.close_phase()
            self.phase = "thinking"
            self._thinking_frame_idx = 0
            self._thinking_widget = self._append(
                thinking_block("", spinner=self._SPINNER_FRAMES[0])
            )
            if self._thinking_spinner_timer is None:
                self._thinking_spinner_timer = self._set_interval(0.1, self._tick_thinking_spinner)
        if text:
            self._thinking_buf.append(text)
        if self._thinking_widget is not None:
            spinner = self._SPINNER_FRAMES[self._thinking_frame_idx]
            self._thinking_widget.update(
                thinking_block("".join(self._thinking_buf).strip(), spinner=spinner)
            )
        self._scroll_end()

    def _tick_thinking_spinner(self) -> None:
        if self._thinking_widget is not None:
            self._thinking_frame_idx = (self._thinking_frame_idx + 1) % len(self._SPINNER_FRAMES)
            spinner = self._SPINNER_FRAMES[self._thinking_frame_idx]
            self._thinking_widget.update(
                thinking_block("".join(self._thinking_buf).strip(), spinner=spinner)
            )

    def _flush_thinking(self) -> None:
        if self._thinking_spinner_timer is not None:
            self._thinking_spinner_timer.stop()
            self._thinking_spinner_timer = None
        widget = self._thinking_widget
        self._thinking_widget = None
        has_text = bool("".join(self._thinking_buf).strip())
        if widget is not None and has_text:
            spinner = self._SPINNER_FRAMES[self._thinking_frame_idx]
            widget.update(thinking_block("".join(self._thinking_buf).strip(), spinner=spinner))
        self._thinking_buf.clear()
        if widget is not None and not has_text:
            widget.remove()

    # ── Answer (streaming markdown) ───────────────────────────────

    def on_text(self, text: str) -> None:
        """Accumulate answer chunks; update the live answer widget in place."""
        if self.phase != "answer":
            self.close_phase()
            self.phase = "answer"
            self._answer_widget = self._append(
                conversation_row(Text("●", style="bold yellow"), Text(""))
            )
        self._answer_buf.append(text)
        if self._answer_widget is not None:
            body = Markdown("".join(self._answer_buf))
            self._answer_widget.update(conversation_row(Text("●", style="bold yellow"), body))
        self._scroll_end()

    def _commit_answer(self) -> None:
        widget = self._answer_widget
        self._answer_widget = None
        if widget is not None and not self._answer_buf:
            widget.remove()
        self._answer_buf.clear()

    # ── Tool calls ────────────────────────────────────────────────

    def on_tool_call(self, name: str, arguments: dict[str, JsonValue]) -> None:
        """Write the tool call signature, plus a colored diff for file edits."""
        self.close_phase()
        signature = format_tool_signature(name, arguments)
        self._active_tool_signature = signature
        self._tool_frame_idx = 0
        self._active_tool_widget = self._append(
            tool_call_widget(signature, spinner=self._SPINNER_FRAMES[0]),
            spaced=False,
        )
        if self._tool_spinner_timer is None:
            self._tool_spinner_timer = self._set_interval(0.1, self._tick_tool_spinner)

        diff = tool_call_diff(name, arguments)
        if diff:
            self._append(diff_renderable(diff), spaced=False)

    def _tick_tool_spinner(self) -> None:
        if self._active_tool_widget is not None:
            self._tool_frame_idx = (self._tool_frame_idx + 1) % len(self._SPINNER_FRAMES)
            spinner = self._SPINNER_FRAMES[self._tool_frame_idx]
            self._active_tool_widget.update(
                tool_call_widget(self._active_tool_signature, spinner=spinner)
            )

    def finalize_tool_call(self, *, is_error: bool) -> None:
        """Stop the tool spinner and update the call-site widget with the final mark."""
        if self._tool_spinner_timer is not None:
            self._tool_spinner_timer.stop()
            self._tool_spinner_timer = None
        if self._active_tool_widget is not None:
            self._active_tool_widget.update(
                tool_call_completed(self._active_tool_signature, is_error=is_error)
            )
            self._active_tool_widget = None

    def finish(self) -> None:
        """Reset all streaming/tool state (called when a Run finishes)."""
        self.close_phase()
        if self._tool_spinner_timer is not None:
            self._tool_spinner_timer.stop()
            self._tool_spinner_timer = None
        self._active_tool_widget = None

    # ── Static rendering helpers ──────────────────────────────────

    def render_user(self, text: str) -> None:
        self._append(user_row(text))

    def render_assistant_footer(self, run_id: str, *, usage: RunUsage | None = None) -> None:
        self._append(assistant_footer(run_id, usage=usage))

    def render_error(self, message: str, *, hint: str | None = None) -> None:
        self.close_phase()
        self._append(Text(f"Error: {message}", style="bold red"), spaced=hint is None)
        if hint:
            self._append(Text(f"Hint: {hint}", style="bold cyan"))

    def render_notification(self, message: str, level: str) -> None:
        prefix = {"info": "· ", "warning": "⚠ ", "error": "✗ "}.get(level, "")
        style = {"info": "dim", "warning": "yellow", "error": "bold red"}.get(level, "dim")
        self._append(
            conversation_row(Text(prefix, style=style), Text(message, style=style)),
            spaced=False,
        )

    def render_command_output(self, content: str, *, is_error: bool) -> None:
        renderable = _render_command_output(content, is_error=is_error)
        if renderable is not None:
            self._append(renderable, spaced=False)
        else:
            self._append(command_summary(content, is_error=is_error), spaced=False)

    def render_tool_result(self, name: str, content: str, *, is_error: bool) -> None:
        self.finalize_tool_call(is_error=is_error)
        renderable = tool_result_block(name, content, is_error=is_error)
        if renderable is not None:
            self._append(renderable, spaced=False)
        else:
            self._append(command_summary(content, is_error=is_error), spaced=False)
