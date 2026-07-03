from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, override

from rich.console import RenderableType
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical, VerticalScroll
from textual.reactive import reactive
from textual.timer import Timer
from textual.widgets import Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option
from textual.worker import Worker

from milky_frog.app.session import AgentSession
from milky_frog.core.controller import RunController
from milky_frog.domain import ApprovalDecision, ApprovalVerdict, ResumeError, RunStatus, RunUsage
from milky_frog.project import load_project_config
from milky_frog.ui.cli import runs_table
from milky_frog.ui.logo import welcome_banner
from milky_frog.ui.messages import (
    AddText,
    AddThinking,
    ApprovalOptionSelected,
    ApprovalRequired,
    BashOutputMsg,
    GitOutputMsg,
    GrepOutputMsg,
    RunError,
    RunFinished,
    RunNoticeMsg,
    ToolCallMsg,
    ToolResultMsg,
    UpdateUsage,
)
from milky_frog.ui.rendering import (
    COMMANDS,
    DiffKind,
    bash_output_renderable,
    complete_command,
    file_change_diff,
    format_tool_call,
    matching_commands,
    summarize_tool_result,
    tool_result_renderable,
)
from milky_frog.ui.usage import context_fraction, format_context_meter, format_run_usage


@dataclass(frozen=True, slots=True)
class TuiLaunch:
    """Optional startup action when the TUI opens from a Typer command."""

    run_id: str | None = None
    prompt: str | None = None
    advance_pending: bool = False


def _row(marker: Text, body: RenderableType) -> Table:
    """A hanging-indent grid: a fixed marker column beside a wrapping body.

    Rendered inside a ``Static``, the ratio body column re-wraps to the widget's
    width, so the conversation reflows when the terminal is resized.
    """
    row = Table.grid(padding=(0, 1))
    row.add_column(no_wrap=True, vertical="top")
    row.add_column(ratio=1)
    row.add_row(marker, body)
    return row


def _thinking(text: str, spinner: str) -> Text:
    """Render the reasoning block: braille spinner frame plus ``thinking``."""
    header = f"{spinner} thinking"
    if not text:
        return Text(header, style="dim italic")
    return Text.assemble((f"{header}\n", "dim italic"), (text, "dim italic"))


# Full-row styles (foreground on background) so each diff line is highlighted
# edge to edge, GitHub/pi-agent style, rather than just the characters.
_DIFF_ROW_STYLE: dict[DiffKind, str] = {
    "add": "#b9f6b0 on #0f2e17",
    "remove": "#f6b0b0 on #2e0f14",
    "context": "dim",
}
_DIFF_SIGN: dict[DiffKind, str] = {"add": "+ ", "remove": "- ", "context": "  "}
_MAX_DIFF_LINES = 40


def _diff_renderable(rows: list[tuple[DiffKind, str]]) -> Table:
    """Render diff rows as full-width highlighted lines (green add, red remove, dim ctx).

    A one-column ``expand``ed grid: each row's style fills the whole line, so the
    highlight spans the terminal width and reflows on resize.
    """
    table = Table.grid(expand=True, padding=(0, 0, 0, 2))
    table.add_column()
    shown = rows[:_MAX_DIFF_LINES]
    for kind, line in shown:
        table.add_row(f"{_DIFF_SIGN[kind]}{line}", style=_DIFF_ROW_STYLE[kind])
    extra = len(rows) - len(shown)
    if extra:
        table.add_row(f"… {extra} more line{'s' if extra != 1 else ''}", style="bright_black")
    return table


# ── Status Widget ──────────────────────────────────────────────────────


class RunStatusBar(Static):
    """Bottom status line showing model, workspace, token usage, and run status."""

    status_text: reactive[str] = reactive("ready")

    def __init__(self, model: str, workspace: Path, context_window: int) -> None:
        super().__init__()
        self._model = model
        self._workspace = _short_workspace(workspace)
        self._context_window = context_window
        self._usage: RunUsage = RunUsage()
        self._run_id: str | None = None
        self._spinner_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self._frame_idx = 0
        self._timer: Timer | None = None

    def watch_status_text(self, value: str) -> None:
        if value in {"working", "cancelling"}:
            if self._timer is None:
                self._timer = self.set_interval(0.1, self._tick)
        else:
            if self._timer is not None:
                self._timer.stop()
                self._timer = None
        self.update(self._format(value))

    def _tick(self) -> None:
        self._frame_idx = (self._frame_idx + 1) % len(self._spinner_frames)
        self.update(self._format(self.status_text))

    def set_usage(self, usage: RunUsage) -> None:
        self._usage = usage
        self.refresh()

    def set_run_id(self, run_id: str) -> None:
        self._run_id = run_id
        self.refresh()

    def set_working(self) -> None:
        self.status_text = "working"

    def set_ready(self) -> None:
        self.status_text = "ready"

    def set_cancelling(self) -> None:
        self.status_text = "cancelling"

    def _format(self, state: str) -> Text:
        parts: list[Text] = []
        parts.append(Text(f" {self._model}", style="dim"))
        parts.append(Text("  ·  ", style="bright_black"))
        parts.append(Text(self._workspace, style="dim"))
        if self._run_id:
            parts.append(Text("  ·  ", style="bright_black"))
            parts.append(Text(f"run {self._run_id[:8]}", style="dim"))
        meter = format_context_meter(self._usage.context_tokens, self._context_window)
        if meter:
            parts.append(Text("  ·  ", style="bright_black"))
            parts.append(Text(meter, style=_context_meter_style(self._usage, self._context_window)))
        usage_str = format_run_usage(self._usage)
        if usage_str:
            parts.append(Text("  ·  ", style="bright_black"))
            parts.append(Text(usage_str, style="dim"))
        parts.append(Text("  ·  ", style="bright_black"))
        if state in {"working", "cancelling"}:
            spinner = self._spinner_frames[self._frame_idx]
            style = "yellow" if state == "working" else "bright_yellow"
            parts.append(Text(f"{spinner} {state}", style=style))
        else:
            parts.append(Text(state, style="green"))
        return Text.assemble(*parts)


def _context_meter_style(usage: RunUsage, context_window: int) -> str:
    """Dim until the context window fills up, then warn (yellow) and alarm (red)."""
    fraction = context_fraction(usage.context_tokens, context_window)
    if fraction is None:
        return "dim"
    if fraction >= 0.9:
        return "bold red"
    if fraction >= 0.75:
        return "yellow"
    return "dim"


def _short_workspace(workspace: Path) -> str:
    resolved = workspace.expanduser().resolve()
    try:
        relative = resolved.relative_to(Path.home())
    except ValueError:
        return resolved.as_posix()
    return f"~/{relative.as_posix()}" if relative.parts else "~"


# ── Prompt input (history + command completion) ───────────────────────


class PromptInput(Input):
    """The task prompt, with Tab command-completion and Up/Down history recall."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._history: list[str] = []
        self._index: int | None = None  # cursor into history; None == live draft
        self._draft: str = ""

    def remember(self, value: str) -> None:
        """Record a submitted prompt and reset the recall cursor."""
        if value and (not self._history or self._history[-1] != value):
            self._history.append(value)
        self._index = None
        self._draft = ""

    async def on_key(self, event: events.Key) -> None:
        # Never intercept text/IME input (e.g. CJK characters): let the base
        # Input insert them. We only handle the navigation keys below.
        if event.is_printable:
            return
        if event.key == "tab":
            completion = complete_command(self.value)
            if completion is not None and completion != self.value:
                event.prevent_default()
                event.stop()
                self.value = completion
                self.cursor_position = len(self.value)
            return
        if event.key == "up":
            event.prevent_default()
            event.stop()
            self._recall_previous()
        elif event.key == "down":
            event.prevent_default()
            event.stop()
            self._recall_next()

    def _recall_previous(self) -> None:
        if not self._history:
            return
        if self._index is None:
            self._draft = self.value
            self._index = len(self._history) - 1
        elif self._index > 0:
            self._index -= 1
        self.value = self._history[self._index]
        self.cursor_position = len(self.value)

    def _recall_next(self) -> None:
        if self._index is None:
            return
        if self._index < len(self._history) - 1:
            self._index += 1
            self.value = self._history[self._index]
        else:
            self._index = None
            self.value = self._draft
        self.cursor_position = len(self.value)


# ── Approval prompt (Claude-style selectable options) ────────────────


def _approval_body(reason: str) -> str:
    """Drop the machine header and keep the user-facing question."""
    _, sep, tail = reason.partition("\n\n")
    return tail.strip() if sep else reason.strip()


class ApprovalPrompt(Vertical):
    """Inline approval menu: arrow keys to highlight, Enter to confirm."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("1", "pick_first", "Allow once", show=False, priority=True),
        Binding("2", "pick_second", "Always this tool", show=False, priority=True),
        Binding("3", "pick_third", "Always all", show=False, priority=True),
        Binding("4", "pick_fourth", "Deny", show=False, priority=True),
        Binding("5", "pick_fifth", "Deny with reason", show=False, priority=True),
    ]

    def __init__(self, *, tool_name: str, reason: str) -> None:
        super().__init__(classes="approval-prompt")
        self._tool_name = tool_name
        self._reason = reason

    @override
    def compose(self) -> ComposeResult:
        header = f"Run {self._tool_name}?" if self._tool_name else "Tool approval required"
        yield Static(Text.assemble(("  ⚠ ", "bold yellow"), (header, "bold")), id="approval-header")
        yield Static(Text(f"  {_approval_body(self._reason)}", style="dim"), id="approval-body")
        options: list[Option] = [
            Option("  1. Allow once", id="approve"),
            Option(
                (
                    f"  2. Always allow {self._tool_name}"
                    if self._tool_name
                    else "  2. Always allow this tool"
                ),
                id="allow_tool",
                disabled=not self._tool_name,
            ),
            Option("  3. Always allow all tools", id="allow_all"),
            Option("  4. Deny", id="deny"),
            Option("  5. Deny and tell the agent why…", id="deny_reason"),
        ]
        yield OptionList(*options, id="approval-options")

    def on_mount(self) -> None:
        self.query_one("#approval-options", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id
        if option_id is not None:
            self.post_message(ApprovalOptionSelected(option_id))

    def _pick(self, index: int) -> None:
        options = self.query_one("#approval-options", OptionList)
        if 0 <= index < options.option_count:
            options.highlighted = index
            options.action_select()

    def action_pick_first(self) -> None:
        self._pick(0)

    def action_pick_second(self) -> None:
        self._pick(1)

    def action_pick_third(self) -> None:
        self._pick(2)

    def action_pick_fourth(self) -> None:
        self._pick(3)

    def action_pick_fifth(self) -> None:
        self._pick(4)


# ── Main App ───────────────────────────────────────────────────────────


class MilkyFrogApp(App[None]):
    """Textual TUI for Milky Frog local coding agent.

    Layout (top to bottom)::

        Header        — model, workspace, clock
        VerticalScroll — conversation: one widget per message, so text reflows
                         to the terminal width on resize (unlike an append-only log)
        command hints — slash-command completions (hidden unless typing a command)
        PromptInput   — text prompt with Tab completion and Up/Down history recall
        RunStatusBar  — model, workspace, run_id, token usage, status
        Footer        — keybindings
    """

    CSS = """
    #conversation {
        border: none;
        height: 1fr;
        margin: 0 1;
    }

    #conversation > .spaced {
        margin-bottom: 1;
    }

    #command-hints {
        display: none;
        height: auto;
        margin: 0 2;
        color: $text-muted;
    }

    #prompt-input {
        margin: 0 1 0 1;
    }

    RunStatusBar {
        height: 1;
        background: $boost;
        padding: 0 1;
    }

    .approval-prompt {
        height: auto;
        margin-bottom: 1;
        border: round yellow;
        padding: 0 1 1 0;
    }

    .approval-prompt OptionList {
        height: auto;
        max-height: 8;
        margin: 0 1;
        background: transparent;
        border: none;
        padding: 0;
    }

    .approval-prompt OptionList > .option-list--option-highlighted {
        background: $accent 20%;
        color: $text;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "request_exit", "Exit"),
        Binding("ctrl+d", "request_exit", "Exit"),
        # priority=True: Input keeps focus during Runs; without this, Esc may never
        # reach the App action while the prompt is focused (Textual 8.x).
        Binding("escape", "cancel_run", "Interrupt", priority=True),
    ]

    def __init__(
        self,
        session: AgentSession,
        run_controller: RunController,
        *,
        launch: TuiLaunch | None = None,
    ) -> None:
        super().__init__()
        self._launch = launch
        self._session = session
        self._run_controller = run_controller
        self._worker: Worker[None] | None = None
        self._pending_approval: ApprovalRequired | None = None
        self._approval_widget: ApprovalPrompt | None = None
        self._approval_deny_reason_mode: bool = False

        # Streaming render state: buffers plus the live widget updated in place.
        self._thinking_buf: list[str] = []
        self._answer_buf: list[str] = []
        self._phase: str | None = None  # None | "thinking" | "answer"
        self._thinking_widget: Static | None = None
        self._answer_widget: Static | None = None

        # Spinner frames and states
        self._spinner_frames: list[str] = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

        self._active_tool_widget: Static | None = None
        self._active_tool_signature: str = ""
        self._tool_spinner_timer: Timer | None = None
        self._tool_frame_idx: int = 0

        self._thinking_spinner_timer: Timer | None = None
        self._thinking_frame_idx: int = 0

    @property
    def session(self) -> AgentSession:
        """The active ``AgentSession``; always valid while the app is running."""
        return self._session

    @property
    def run_controller(self) -> RunController:
        return self._run_controller

    @override
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Vertical(
            # Not focusable: the prompt must keep focus so typing (incl. IME
            # composition) always lands in the input; mouse-wheel scroll still works.
            VerticalScroll(id="conversation", can_focus=False),
            Static(id="command-hints"),
            PromptInput(id="prompt-input", placeholder="Type a task and press Enter..."),
            RunStatusBar(
                model=self.session.model_name or "unknown",
                workspace=Path.cwd(),
                context_window=load_project_config(Path.cwd()).context_window,
            ),
        )
        yield Footer()

    def on_mount(self) -> None:
        """App started: render welcome."""
        self._render_welcome()
        self.query_one("#prompt-input", Input).focus()
        if self._launch is not None:
            self.call_after_refresh(self._apply_launch)

    def _apply_launch(self) -> None:
        launch = self._launch
        if launch is None:
            return
        if launch.run_id is None:
            if launch.prompt:
                self._start_run(launch.prompt)
            return
        self._attach_or_continue_run(
            launch.run_id,
            prompt=launch.prompt,
            advance_pending=launch.advance_pending,
        )

    # ── Conversation plumbing ─────────────────────────────────────────

    def _conversation(self) -> VerticalScroll:
        return self.query_one("#conversation", VerticalScroll)

    def _append(self, renderable: RenderableType, *, spaced: bool = True) -> Static:
        """Mount a message widget at the bottom of the conversation and scroll to it."""
        widget = Static(renderable)
        if spaced:
            widget.add_class("spaced")
        self._conversation().mount(widget)
        self._scroll_end()
        return widget

    def _scroll_end(self) -> None:
        self.call_after_refresh(self._conversation().scroll_end, animate=False)

    # ── Rendering helpers ─────────────────────────────────────────────

    def _render_welcome(self) -> None:
        self._append(welcome_banner())
        self._append(Text("Welcome to MILKY FROG · 奶蛙", style="bold yellow"))
        self._append(
            Text.assemble(
                ("Describe what to build, fix, or explain — be specific\n", "dim"),
                ("/help lists commands · /clear resets · /exit leaves", "dim"),
            )
        )

    def _render_user_message(self, text: str) -> None:
        self._append(_row(Text("▸", style="bold cyan"), Text(text)))

    def _tick_tool_spinner(self) -> None:
        if self._active_tool_widget is not None:
            self._tool_frame_idx = (self._tool_frame_idx + 1) % len(self._spinner_frames)
            spinner = self._spinner_frames[self._tool_frame_idx]
            self._active_tool_widget.update(
                Text.assemble(
                    (f"  {spinner} ", "bold yellow"), (self._active_tool_signature, "cyan")
                )
            )

    def _tick_thinking_spinner(self) -> None:
        if self._thinking_widget is not None:
            self._thinking_frame_idx = (self._thinking_frame_idx + 1) % len(self._spinner_frames)
            spinner = self._spinner_frames[self._thinking_frame_idx]
            self._thinking_widget.update(
                _thinking("".join(self._thinking_buf).strip(), spinner=spinner)
            )

    def _flush_thinking(self) -> None:
        """Finalize the live reasoning widget (it already holds the streamed body)."""
        if self._thinking_spinner_timer is not None:
            self._thinking_spinner_timer.stop()
            self._thinking_spinner_timer = None

        widget = self._thinking_widget
        self._thinking_widget = None
        has_text = bool("".join(self._thinking_buf).strip())

        if widget is not None and has_text:
            spinner = self._spinner_frames[self._thinking_frame_idx]
            widget.update(_thinking("".join(self._thinking_buf).strip(), spinner))

        self._thinking_buf.clear()
        if widget is not None and not has_text:
            widget.remove()  # no reasoning was produced; drop the bare header

    def _commit_answer(self) -> None:
        """Finalize the live answer widget (it already holds the streamed markdown)."""
        widget = self._answer_widget
        self._answer_widget = None
        if widget is not None and not self._answer_buf:
            widget.remove()  # nothing streamed; drop the empty bullet
        self._answer_buf.clear()

    def _render_assistant_footer(self, run_id: str, *, usage: RunUsage | None = None) -> None:
        footer = f"  ⎿ run {run_id[:8]}"
        summary = format_run_usage(usage) if usage is not None else None
        if summary is not None:
            footer = f"{footer} · {summary}"
        self._append(Text(footer, style="bright_black"))

    def _render_error(self, message: str, *, hint: str | None = None) -> None:
        self._close_phase()
        self._append(Text(f"Error: {message}", style="bold red"), spaced=hint is None)
        if hint:
            self._append(Text(f"Hint: {hint}", style="bold cyan"))

    def _render_notification(self, message: str, level: str) -> None:
        prefix = {"info": "· ", "warning": "⚠ ", "error": "✗ "}.get(level, "")
        style = {"info": "dim", "warning": "yellow", "error": "bold red"}.get(level, "dim")
        self._append(_row(Text(prefix, style=style), Text(message, style=style)), spaced=False)

    def _render_help(self) -> None:
        commands = Table.grid(padding=(0, 2))
        commands.add_column(style="yellow", no_wrap=True)
        commands.add_column(style="dim")
        for command in COMMANDS:
            commands.add_row(command.usage or command.name, command.description)
        commands.add_row("", "")
        commands.add_row("Tab", "Complete a slash command")
        commands.add_row("↑ / ↓", "Recall previous prompts")
        commands.add_row("Esc", "Interrupt the running task")
        self._append(Panel(commands, title="Commands", border_style="bright_black"))

    def _render_runs(self) -> None:
        runs = self.session.checkpoints.list_runs()
        self._append(
            Panel(runs_table(runs), title="Recent runs", border_style="bright_black"),
        )

    def _close_phase(self) -> None:
        """Close the current streaming phase, committing any buffered content."""
        if self._phase == "thinking":
            self._flush_thinking()
        elif self._phase == "answer":
            self._commit_answer()
        self._phase = None

    # ── Message handlers (lifecycle streaming) ────────────────────────

    def on_add_thinking(self, event: AddThinking) -> None:
        """Accumulate reasoning chunks; update the live reasoning widget in place."""
        if self._phase != "thinking":
            self._close_phase()
            self._phase = "thinking"
            self._thinking_frame_idx = 0
            self._thinking_widget = self._append(_thinking("", spinner=self._spinner_frames[0]))
            if self._thinking_spinner_timer is None:
                self._thinking_spinner_timer = self.set_interval(0.1, self._tick_thinking_spinner)
        if event.text:
            self._thinking_buf.append(event.text)
        if self._thinking_widget is not None:
            spinner = self._spinner_frames[self._thinking_frame_idx]
            self._thinking_widget.update(
                _thinking("".join(self._thinking_buf).strip(), spinner=spinner)
            )
        self._scroll_end()

    def on_add_text(self, event: AddText) -> None:
        """Accumulate answer chunks; update the live answer widget in place."""
        if self._phase != "answer":
            self._close_phase()
            self._phase = "answer"
            self._answer_widget = self._append(_row(Text("●", style="bold yellow"), Text("")))
        self._answer_buf.append(event.text)
        if self._answer_widget is not None:
            body = Markdown("".join(self._answer_buf))
            self._answer_widget.update(_row(Text("●", style="bold yellow"), body))
        self._scroll_end()

    def on_tool_call_msg(self, event: ToolCallMsg) -> None:
        """Write the tool call signature, plus a colored diff for file edits."""
        self._close_phase()
        signature = format_tool_call(event.name, event.arguments)
        self._active_tool_signature = signature
        self._tool_frame_idx = 0
        self._active_tool_widget = self._append(
            Text.assemble((f"  {self._spinner_frames[0]} ", "bold yellow"), (signature, "cyan")),
            spaced=False,
        )
        if self._tool_spinner_timer is None:
            self._tool_spinner_timer = self.set_interval(0.1, self._tick_tool_spinner)

        diff = file_change_diff(event.name, event.arguments)
        if diff:
            self._append(_diff_renderable(diff), spaced=False)

    def _finalize_tool_call(self, *, is_error: bool) -> None:
        """Stop the tool spinner and update the call-site widget with the final mark."""
        if self._tool_spinner_timer is not None:
            self._tool_spinner_timer.stop()
            self._tool_spinner_timer = None
        if self._active_tool_widget is not None:
            mark, style = ("✗", "bold red") if is_error else ("⏺", "bold cyan")
            self._active_tool_widget.update(
                Text.assemble((f"  {mark} ", style), (self._active_tool_signature, "cyan"))
            )
            self._active_tool_widget = None

    def _render_command_output(self, content: str, *, is_error: bool) -> None:
        """Render bash-family output: full inline block when non-empty, summary otherwise."""
        renderable = bash_output_renderable(content, is_error=is_error)
        if renderable is not None:
            self._append(renderable, spaced=False)
        else:
            summary = summarize_tool_result(content, is_error=is_error)
            mark, style = ("✗", "red") if is_error else ("⎿", "bright_black")
            self._append(Text.assemble((f"    {mark} ", style), (summary, "dim")), spaced=False)

    def on_tool_result_msg(self, event: ToolResultMsg) -> None:
        """Non-bash tool result: stop spinner and show a compact summary line."""
        self._finalize_tool_call(is_error=event.is_error)
        renderable = tool_result_renderable(event.name, event.content, is_error=event.is_error)
        if renderable is not None:
            self._append(renderable, spaced=False)
        else:
            summary = summarize_tool_result(event.content, is_error=event.is_error)
            mark, style = ("✗", "red") if event.is_error else ("⎿", "bright_black")
            self._append(Text.assemble((f"    {mark} ", style), (summary, "dim")), spaced=False)

    def on_git_output_msg(self, event: GitOutputMsg) -> None:
        """git command result: stop spinner and render with ANSI colors."""
        self._finalize_tool_call(is_error=event.is_error)
        self._render_command_output(event.content, is_error=event.is_error)

    def on_grep_output_msg(self, event: GrepOutputMsg) -> None:
        """grep/rg result: stop spinner and render matches inline."""
        self._finalize_tool_call(is_error=event.is_error)
        self._render_command_output(event.content, is_error=event.is_error)

    def on_bash_output_msg(self, event: BashOutputMsg) -> None:
        """Generic bash result: stop spinner and render output inline."""
        self._finalize_tool_call(is_error=event.is_error)
        self._render_command_output(event.content, is_error=event.is_error)

    def on_update_usage(self, event: UpdateUsage) -> None:
        self.query_one(RunStatusBar).set_usage(event.usage)

    def on_run_finished(self, event: RunFinished) -> None:
        """Run finished: commit any buffered content and show the footer."""
        self._worker = None
        self.session.attach_worker(None)
        self._close_phase()

        if self._tool_spinner_timer is not None:
            self._tool_spinner_timer.stop()
            self._tool_spinner_timer = None
        self._active_tool_widget = None

        status_bar = self.query_one(RunStatusBar)

        if event.status is RunStatus.COMPLETED:
            self._render_assistant_footer(event.result.run_id, usage=event.result.usage)
        elif event.status is RunStatus.CANCELLED:
            self._render_error(event.message, hint="Type a new prompt, or Ctrl+C to exit.")
        elif event.status is RunStatus.FAILED or event.status is RunStatus.PAUSED_LIMIT:
            self._render_error(event.message)

        status_bar.set_run_id(event.result.run_id)
        status_bar.set_usage(event.result.usage or RunUsage())
        status_bar.set_ready()
        self.query_one("#prompt-input", Input).disabled = False
        self.query_one("#prompt-input", Input).focus()

    def on_run_error(self, event: RunError) -> None:
        self._worker = None
        self.session.attach_worker(None)
        self._close_phase()
        self._render_error(event.error)
        self.query_one(RunStatusBar).set_ready()
        self.query_one("#prompt-input", Input).disabled = False
        self.query_one("#prompt-input", Input).focus()

    def on_run_notice_msg(self, event: RunNoticeMsg) -> None:
        self._render_notification(event.message, event.level)

    def _parse_approval_input(self, text: str) -> ApprovalVerdict | str | None:
        """Parse user input as an approval decision.

        Returns ``ApprovalVerdict`` for approve/deny, ``"allow_tool"`` or
        ``"allow_all"`` for session-policy overrides, or ``None`` if the input
        is unrecognised.
        """
        lowered = text.strip().lower()

        if lowered in ("y", "yes", "approve"):
            return ApprovalVerdict(ApprovalDecision.APPROVE)
        if lowered in ("n", "no", "deny"):
            return ApprovalVerdict(ApprovalDecision.DENY)

        for prefix in ("no because ", "n because "):
            if lowered.startswith(prefix):
                reason = text.strip()[len(prefix) :].strip()
                return ApprovalVerdict(ApprovalDecision.DENY, denial_reason=reason)

        if lowered in ("always", "always allow"):
            return "allow_tool"
        if lowered in (
            "always all",
            "always_all",
            "alwaysall",
            "don't ask again",
            "dont ask again",
        ):
            return "allow_all"

        return None

    def on_approval_required(self, event: ApprovalRequired) -> None:
        """A tool call needs the user's verdict: show a selectable option menu."""
        self._close_phase()
        self.session.pending_approval = event.run_id
        self._pending_approval = event
        self._approval_deny_reason_mode = False

        prompt = ApprovalPrompt(tool_name=event.tool_name, reason=event.reason)
        self._approval_widget = prompt
        self._conversation().mount(prompt)
        self._scroll_end()

        self.session.busy = False
        prompt_input = self.query_one("#prompt-input", PromptInput)
        prompt_input.disabled = True
        prompt_input.placeholder = "Type a task and press Enter..."

    def on_approval_option_selected(self, event: ApprovalOptionSelected) -> None:
        """Apply the highlighted approval choice."""
        if self._pending_approval is None:
            return
        if event.action == "deny_reason":
            self._begin_deny_reason_input()
            return
        self._apply_approval_action(event.action)

    def _begin_deny_reason_input(self) -> None:
        """Switch from the option menu to a free-text denial reason."""
        self._approval_deny_reason_mode = True
        if self._approval_widget is not None:
            self._approval_widget.remove()
            self._approval_widget = None
        self._append(
            Text("  Type why you're denying, then press Enter.", style="bold yellow"),
            spaced=False,
        )
        prompt_input = self.query_one("#prompt-input", PromptInput)
        prompt_input.disabled = False
        prompt_input.placeholder = "Reason for denial…"
        prompt_input.focus()

    def _clear_approval_ui(self) -> None:
        self._pending_approval = None
        self.session.pending_approval = None
        self._approval_deny_reason_mode = False
        if self._approval_widget is not None:
            self._approval_widget.remove()
            self._approval_widget = None
        prompt_input = self.query_one("#prompt-input", PromptInput)
        prompt_input.placeholder = "Type a task and press Enter..."

    def _apply_approval_action(self, action: str) -> None:
        """Resolve a pending approval from a menu action id."""
        event = self._pending_approval
        if event is None:
            return
        self._clear_approval_ui()

        if action == "approve":
            self._start_approval(event.run_id, ApprovalVerdict(ApprovalDecision.APPROVE))
        elif action == "deny":
            self._start_approval(event.run_id, ApprovalVerdict(ApprovalDecision.DENY))
        elif action == "allow_tool":
            if event.tool_name:
                self._session.policy.allow(event.tool_name)
            self._start_approval(event.run_id, ApprovalVerdict(ApprovalDecision.APPROVE))
        elif action == "allow_all":
            self._session.policy.auto_approve()
            self._start_approval(event.run_id, ApprovalVerdict(ApprovalDecision.APPROVE))

    def _handle_approval_input(self, text: str) -> None:
        """Parse typed approval shorthand (y/n/always) as a fallback."""
        event = self._pending_approval
        if event is None:
            return

        if self._approval_deny_reason_mode:
            reason = text.strip()
            if not reason:
                self._append(
                    Text("  Please enter a reason, or Esc to cancel.", style="bold yellow"),
                    spaced=False,
                )
                return
            run_id = event.run_id
            self._clear_approval_ui()
            self._start_approval(
                run_id,
                ApprovalVerdict(ApprovalDecision.DENY, denial_reason=reason),
            )
            return

        verdict = self._parse_approval_input(text)
        if verdict is None:
            self._append(
                Text(
                    "  Use ↑/↓ and Enter on the menu, or type: "
                    "y / n / n because <reason> / always / always all",
                    style="bold yellow",
                ),
                spaced=False,
            )
            return

        if isinstance(verdict, str):
            self._apply_approval_action(verdict)
        else:
            run_id = event.run_id
            self._clear_approval_ui()
            self._start_approval(run_id, verdict)

    # ── Input handling ────────────────────────────────────────────────

    def on_input_changed(self, message: Input.Changed) -> None:
        """Show slash-command completions while a command is being typed."""
        hints = self.query_one("#command-hints", Static)
        value = message.value
        matches = matching_commands(value) if value.startswith("/") and " " not in value else ()
        if not matches:
            hints.display = False
            return
        if len(matches) == 1:
            only = matches[0]
            hints.update(
                Text.assemble(
                    (only.name, "bold yellow"),
                    ("  " + (only.usage or only.description), "dim"),
                    ("   ⏎/Tab", "bright_black"),
                )
            )
        else:
            row = Text()
            for index, command in enumerate(matches):
                if index:
                    row.append("   ")
                row.append(command.name, style="yellow")
            row.append("   Tab to complete", style="bright_black")
            hints.update(row)
        hints.display = True

    def on_input_submitted(self, message: Input.Submitted) -> None:
        """Handle a user prompt submission."""
        task = message.value.strip()
        if not task:
            return

        prompt_input = self.query_one("#prompt-input", PromptInput)
        prompt_input.remember(task)
        prompt_input.clear()
        self.query_one("#command-hints", Static).display = False

        # Pending approval?  Menu is focused; typed input is a fallback or denial reason.
        if self._pending_approval is not None:
            if self._approval_deny_reason_mode or not self._approval_widget:
                self._handle_approval_input(task)
            return

        if self.session.busy:
            return

        command = task.casefold()
        if command in {"exit", "quit", "/exit"}:
            self._prepare_shutdown()
            self.exit()
            return

        if command in {"?", "/help"}:
            self._render_help()
            return
        if command == "/runs":
            self._render_runs()
            return
        if command == "/clear":
            self._conversation().remove_children()
            self.session.run_id = None
            return

        if command.startswith("/resume"):
            self._handle_resume(task)
            return

        self._start_run(task, run_id=self.session.run_id)

    def _handle_resume(self, task: str) -> None:
        """Parse ``/resume`` and either attach to a Run or continue it."""
        parsed = self.run_controller.parse_resume_command(task)
        if isinstance(parsed, str):
            hint = None
            if parsed == "No runs found to resume.":
                hint = "Start a new task to create a run first."
            self._render_error(parsed, hint=hint)
            return
        self._attach_or_continue_run(
            parsed.run_id,
            prompt=parsed.prompt,
            advance_pending=False,
        )

    def _attach_or_continue_run(
        self,
        run_id: str,
        *,
        prompt: str | None = None,
        advance_pending: bool = False,
    ) -> None:
        """Attach to a Run, continue with a prompt, or advance pending work."""
        outcome = self.run_controller.attach(
            run_id,
            prompt=prompt,
            advance_pending=advance_pending,
        )

        if outcome.kind == "prompt_continue":
            self.session.run_id = outcome.run_id
            self.query_one(RunStatusBar).set_run_id(outcome.run_id)
            self._start_run(prompt or "", run_id=outcome.run_id)
            return

        self.session.run_id = outcome.run_id
        self.query_one(RunStatusBar).set_run_id(outcome.run_id)

        if outcome.kind == "approval_pending":
            self.post_message(
                ApprovalRequired(outcome.run_id, outcome.approval_reason, outcome.tool_name)
            )
            self._append(
                Text(
                    f"Attached to run {outcome.run_id[:8]} · pending tool approval",
                    style="dim",
                )
            )
            return

        if outcome.kind == "advance":
            self._append(Text(f"Resuming run {outcome.run_id[:8]}…", style="dim"))
            self._start_continue_pending(outcome.run_id)
            return

        self._append(
            Text(
                f"Attached to run {outcome.run_id[:8]} · next prompt continues it",
                style="dim",
            )
        )

    def _start_run(self, task: str, *, run_id: str | None = None) -> None:
        """Kick off a Run as a Textual worker."""
        # Set the busy guard synchronously so on_input_submitted rejects
        # double-submits before the worker's first yield point.
        self.session.busy = True
        self._phase = None
        self._thinking_buf.clear()
        self._answer_buf.clear()
        self._thinking_widget = None
        self._answer_widget = None

        self._render_user_message(task)
        self.query_one(RunStatusBar).set_working()
        self.query_one("#prompt-input", PromptInput).disabled = True

        self._worker = self._do_run(task, run_id)
        self.session.attach_worker(self._worker.cancel)

    def _start_continue_pending(self, run_id: str) -> None:
        """Advance a Run with pending work and no new user turn."""
        self.session.run_id = run_id
        self.session.busy = True
        self._phase = None
        self._thinking_buf.clear()
        self._answer_buf.clear()
        self._thinking_widget = None
        self._answer_widget = None

        self.query_one(RunStatusBar).set_run_id(run_id)
        self.query_one(RunStatusBar).set_working()
        self.query_one("#prompt-input", PromptInput).disabled = True

        self._worker = self._do_continue(run_id)
        self.session.attach_worker(self._worker.cancel)

    @work(thread=False, exit_on_error=False)
    async def _do_continue(self, run_id: str) -> None:
        """Thin worker: advance pending work without a new user message."""
        try:
            await self.session.continue_with(run_id)
        except ResumeError as error:
            self.post_message(RunError(str(error)))
        except asyncio.CancelledError:
            result = AgentSession.cancelled_result(run_id)
            self.post_message(
                RunFinished(
                    result=result,
                    status=RunStatus.CANCELLED,
                    message="Cancelled the current task.",
                )
            )
            raise

    @work(thread=False, exit_on_error=False)
    async def _do_run(self, task: str, run_id: str | None) -> None:
        """Thin worker: delegate to AgentSession; UI via TuiPresentationHandler."""
        try:
            if run_id is None:
                await self.session.start_new(task)
            else:
                await self.session.continue_with(run_id, prompt=task)
        except ResumeError as error:
            self.post_message(RunError(str(error)))
        except asyncio.CancelledError:
            result = AgentSession.cancelled_result(self.session.run_id)
            self.post_message(
                RunFinished(
                    result=result,
                    status=RunStatus.CANCELLED,
                    message="Cancelled the current task.",
                )
            )
            raise

    def _start_approval(self, run_id: str, verdict: ApprovalVerdict) -> None:
        """Resume a paused Run with the user's approval verdict."""
        self.session.busy = True
        self._phase = None
        self._thinking_buf.clear()
        self._answer_buf.clear()
        self._thinking_widget = None
        self._answer_widget = None

        verb = "Approved" if verdict.decision is ApprovalDecision.APPROVE else "Denied"
        if verdict.denial_reason:
            verb += f" (reason: {verdict.denial_reason})"
        self._append(_row(Text("▸", style="bold cyan"), Text(verb, style="dim")))
        self.query_one(RunStatusBar).set_working()
        self.query_one("#prompt-input", PromptInput).disabled = True

        self._worker = self._do_approve(run_id, verdict)
        self.session.attach_worker(self._worker.cancel)

    @work(thread=False, exit_on_error=False)
    async def _do_approve(self, run_id: str, verdict: ApprovalVerdict) -> None:
        """Thin worker: delegate to AgentSession; UI via TuiPresentationHandler."""
        try:
            await self.session.respond_approval(run_id, verdict)
        except ResumeError as error:
            self.post_message(RunError(str(error)))
        except asyncio.CancelledError:
            result = AgentSession.cancelled_result(run_id)
            self.post_message(
                RunFinished(
                    result=result,
                    status=RunStatus.CANCELLED,
                    message="Cancelled the current task.",
                )
            )
            raise

    # ── Key bindings ──────────────────────────────────────────────────

    def action_request_exit(self) -> None:
        self._request_exit()

    def _request_exit(self) -> None:
        """Route exit actions through cooperative shutdown."""
        self._prepare_shutdown()
        self.exit()

    def _prepare_shutdown(self) -> None:
        """Cooperatively stop an in-flight Run before the TUI closes.

        Idempotent: ``ShutdownManager`` guards against double-cancel when
        overlapping callers converge on the same shutdown path.
        """
        self.session.request_shutdown()

    def action_cancel_run(self) -> None:
        """Interrupt the in-flight Run, if any (Esc).

        Signal cooperative cancellation first so the Harness can reach
        ``finish_cancelled`` (persisting CANCELLED with the real run_id), then
        hard-cancel the Textual worker so Esc still works when the stream or a
        Tool is slow to honour the token.
        """
        if not self.session.busy:
            return
        self.query_one(RunStatusBar).set_cancelling()
        self.session.cancel()
        if self._worker is not None:
            self._worker.cancel()
