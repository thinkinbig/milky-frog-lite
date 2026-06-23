from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar

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
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Footer, Header, Input, ListItem, ListView, Static
from textual.worker import Worker

from milky_frog.agent_session import AgentSession
from milky_frog.domain import ApprovalDecision, ApprovalVerdict, ResumeError, RunStatus, RunUsage
from milky_frog.settings import Settings
from milky_frog.ui.logo import pixel_frog_logo
from milky_frog.ui.tui.assembly import tui_presentation_bundle
from milky_frog.ui.tui.messages import (
    AddText,
    AddThinking,
    ApprovalRequired,
    RunError,
    RunFinished,
    RunNoticeMsg,
    ToolCallMsg,
    ToolResultMsg,
    UpdateUsage,
)
from milky_frog.ui.tui.rendering import (
    COMMANDS,
    DiffKind,
    complete_command,
    file_change_diff,
    format_tool_call,
    matching_commands,
    summarize_tool_result,
)
from milky_frog.ui.tui.textual_patch import patch_textual_utf8_decode
from milky_frog.ui.usage import format_run_usage


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

    def __init__(self, model: str, workspace: Path) -> None:
        super().__init__()
        self._model = model
        self._workspace = _short_workspace(workspace)
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


# ── Main App ───────────────────────────────────────────────────────────


class ApprovalDialog(ModalScreen[ApprovalVerdict | str]):
    """Modal dialog for tool call approval with session-policy quick actions.

    Multi-phase interaction:
      1. SELECT — choose from Yes / No / No, provide reason / Always allow
      2. INPUT_REASON — when "No, provide reason" is selected, show a text
         input for the denial reason, then dismiss with the full verdict.

    Returns ``ApprovalVerdict`` for Yes/No, or ``"allow_tool"`` / ``"allow_all"``
    to apply a session-level policy override before approving.
    """

    CSS = """
    ApprovalDialog {
        align: center middle;
    }

    #approval-dialog-box {
        width: 60;
        height: auto;
        padding: 1 2;
        border: thick $secondary;
        background: $surface;
    }

    #approval-message {
        margin-bottom: 1;
    }

    ListView {
        height: auto;
        margin: 0 1;
        border: none;
    }

    ListItem {
        padding: 0 1;
    }

    ListItem:focus {
        background: $accent;
    }

    #approval-help {
        margin-top: 1;
        color: $text-muted;
    }

    #reason-input {
        display: none;
        margin: 0 1;
    }
    #reason-input.-visible {
        display: block;
    }

    #reason-help {
        display: none;
        margin-top: 1;
        color: $text-muted;
    }
    #reason-help.-visible {
        display: block;
    }
    """

    def __init__(self, reason: str, tool_name: str = "") -> None:
        super().__init__()
        self.message = reason
        self.tool_name = tool_name

    def compose(self) -> ComposeResult:
        with Vertical(id="approval-dialog"), Vertical(id="approval-dialog-box"):
            yield Static(self.message, id="approval-message")
            yield ListView(
                ListItem(Static("  1. Yes"), name="approve"),
                ListItem(Static("  2. No"), name="deny"),
                ListItem(Static("  3. No, provide reason"), name="deny_with_reason"),
                ListItem(Static("  4. Always allow this tool"), name="allow_tool"),
                ListItem(Static("  5. Don\u2019t ask again this session"), name="allow_all"),
                initial_index=0,
                id="option-list",
            )
            yield Static(
                "Enter to select \u00b7 1-5 to choose \u00b7 Esc to deny",
                id="approval-help",
            )
            yield Input(
                placeholder="Reason shown back to the agent (optional)...",
                id="reason-input",
            )
            yield Static(
                "Type reason and press Enter, or Esc to go back",
                id="reason-help",
            )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        name = event.item.name
        if name == "approve":
            self.dismiss(ApprovalVerdict(ApprovalDecision.APPROVE))
        elif name == "deny_with_reason":
            self._show_reason_input()
        elif name == "allow_tool":
            self.dismiss("allow_tool")
        elif name == "allow_all":
            self.dismiss("allow_all")
        else:
            self.dismiss(ApprovalVerdict(ApprovalDecision.DENY))

    def on_key(self, event: events.Key) -> None:
        if event.key == "1":
            event.stop()
            self.dismiss(ApprovalVerdict(ApprovalDecision.APPROVE))
        elif event.key == "2":
            event.stop()
            self.dismiss(ApprovalVerdict(ApprovalDecision.DENY))
        elif event.key == "3":
            event.stop()
            self._show_reason_input()
        elif event.key == "4":
            event.stop()
            self.dismiss("allow_tool")
        elif event.key == "5":
            event.stop()
            self.dismiss("allow_all")
        elif event.key == "escape":
            event.stop()
            self.dismiss(ApprovalVerdict(ApprovalDecision.DENY))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        reason = event.value.strip()
        self.dismiss(
            ApprovalVerdict(
                ApprovalDecision.DENY,
                denial_reason=reason or None,
            )
        )

    def _show_reason_input(self) -> None:
        """Transition to the reason-input phase."""
        self.query_one("#option-list", ListView).display = False
        self.query_one("#approval-help", Static).display = False
        reason_input = self.query_one("#reason-input", Input)
        reason_input.display = True
        reason_input.add_class("-visible")
        self.query_one("#reason-help", Static).display = True
        self.query_one("#reason-help", Static).add_class("-visible")
        reason_input.focus()


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
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "request_exit", "Exit"),
        Binding("ctrl+d", "request_exit", "Exit"),
        # priority=True: Input keeps focus during Runs; without this, Esc may never
        # reach the App action while the prompt is focused (Textual 8.x).
        Binding("escape", "cancel_run", "Interrupt", priority=True),
    ]

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        # Create the async Session now (without entering it — entering happens
        # in ``run()`` so Textual's sync outer loop can bracket the lifecycle).
        self._session = AgentSession(
            settings,
            bundles=[tui_presentation_bundle(self.post_message)],
        )
        self._worker: Worker[None] | None = None
        self._approval_event: ApprovalRequired | None = None

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
        """The active ``Session``; always valid while the app is running."""
        return self._session

    def run(self, *args: Any, **kwargs: Any) -> Any:
        """Run the TUI with a managed ``Session`` lifecycle.

        ``Session.__aenter__`` opens async resources (model connection, handler
        sessions); ``__aexit__`` releases them.  A single ``asyncio.run()`` brackets
        the session and Textual's ``run_async()`` so we never nest event loops.
        """
        patch_textual_utf8_decode()

        async def _run_with_session() -> Any:
            async with self._session:
                return await self.run_async(*args, **kwargs)

        return asyncio.run(_run_with_session())

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
            ),
        )
        yield Footer()

    def on_mount(self) -> None:
        """App started: render welcome."""
        self._render_welcome()
        self.query_one("#prompt-input", Input).focus()

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
        welcome = Table.grid(padding=(0, 3))
        welcome.add_column(no_wrap=True)
        welcome.add_column(overflow="fold")
        welcome.add_row(
            pixel_frog_logo(),
            Text("✻ Welcome to MILKY FROG · 奶蛙", style="bold yellow"),
        )
        self._append(Panel(welcome, border_style="yellow"))
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

    def on_tool_result_msg(self, event: ToolResultMsg) -> None:
        """Write the tool result as an indented summary line under its call."""
        if self._tool_spinner_timer is not None:
            self._tool_spinner_timer.stop()
            self._tool_spinner_timer = None

        if self._active_tool_widget is not None:
            mark, style = ("✗", "bold red") if event.is_error else ("⏺", "bold cyan")
            self._active_tool_widget.update(
                Text.assemble((f"  {mark} ", style), (self._active_tool_signature, "cyan"))
            )
            self._active_tool_widget = None

        summary = summarize_tool_result(event.content, is_error=event.is_error)
        mark, style = ("✗", "red") if event.is_error else ("⎿", "bright_black")
        self._append(Text.assemble((f"    {mark} ", style), (summary, "dim")))

    def on_update_usage(self, event: UpdateUsage) -> None:
        self.query_one(RunStatusBar).set_usage(event.usage)

    def on_run_finished(self, event: RunFinished) -> None:
        """Run finished: commit any buffered content and show the footer."""
        self._worker = None
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
        # elif event.status is RunStatus.WAITING_FOR_APPROVAL:
        #     # The approval prompt is rendered by on_approval_required (driven by
        #     # the RunPaused lifecycle signal); nothing more to show here.
        #     pass
        elif event.status is RunStatus.FAILED or event.status is RunStatus.PAUSED_LIMIT:
            self._render_error(event.message)

        status_bar.set_run_id(event.result.run_id)
        status_bar.set_usage(event.result.usage or RunUsage())
        status_bar.set_ready()
        self.query_one("#prompt-input", Input).disabled = False
        self.query_one("#prompt-input", Input).focus()

    def on_run_error(self, event: RunError) -> None:
        self._worker = None
        self._close_phase()
        self._render_error(event.error)
        self.query_one(RunStatusBar).set_ready()
        self.query_one("#prompt-input", Input).disabled = False
        self.query_one("#prompt-input", Input).focus()

    def on_run_notice_msg(self, event: RunNoticeMsg) -> None:
        self._render_notification(event.message, event.level)

    def on_approval_required(self, event: ApprovalRequired) -> None:
        """A tool call needs the user's verdict: show an approval dialog."""
        self._close_phase()
        self.session.pending_approval = event.run_id
        self.query_one("#prompt-input", PromptInput).disabled = True
        self._approval_event = event
        self.push_screen(
            ApprovalDialog(event.reason, event.tool_name),
            self._handle_approval_verdict,
        )

    def _handle_approval_verdict(
        self,
        verdict: ApprovalVerdict | str | None,
    ) -> None:
        """Process the approval dialog result and optionally apply session policy."""
        event = self._approval_event
        if event is None or verdict is None:
            self._approval_event = None
            return
        self.session.pending_approval = None
        if isinstance(verdict, str):
            if verdict == "allow_tool":
                if event.tool_name:
                    self._session.policy.allow(event.tool_name)
                self._start_approval(event.run_id, ApprovalVerdict(ApprovalDecision.APPROVE))
            elif verdict == "allow_all":
                self._session.policy.auto_approve()
                self._start_approval(event.run_id, ApprovalVerdict(ApprovalDecision.APPROVE))
        else:
            self._start_approval(event.run_id, verdict)
        self._approval_event = None

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
        if self.session.busy:
            return
        task = message.value.strip()
        if not task:
            return

        prompt_input = self.query_one("#prompt-input", PromptInput)
        prompt_input.remember(task)
        prompt_input.clear()
        self.query_one("#command-hints", Static).display = False

        command = task.casefold()
        if command in {"exit", "quit", "/exit"}:
            self.exit()
            return

        if command in {"?", "/help"}:
            self._render_help()
            return
        if command == "/clear":
            self._conversation().remove_children()
            self.session.run_id = None
            return

        if command.startswith("/resume"):
            self._handle_resume(task)
            return

        self._start_run(task)

    def _handle_resume(self, task: str) -> None:
        """Parse ``/resume`` and either attach to a Run or continue it.

        Without a run_id, defaults to the most recently updated run:

          ``/resume``               — attach to the latest run
          ``/resume RUN_ID``         — attach to a specific run
          ``/resume prompt``         — continue the latest run with a prompt
          ``/resume RUN_ID prompt``  — continue a specific run with a prompt
        """
        rest = task[len("/resume") :].strip()
        head, _, tail = rest.partition(" ")
        head = head.strip()
        tail = tail.strip()

        if head:
            try:
                run_id = self.session.checkpoints.resolve_run_id(head)
            except (LookupError, ValueError) as error:
                self._render_error(f"unknown Run: {error}")
                return
        else:
            runs = self.session.checkpoints.list_runs(limit=1)
            if not runs:
                self._render_error(
                    "No runs found to resume.",
                    hint="Start a new task to create a run first.",
                )
                return
            run_id = runs[0].run_id

        prompt = tail or None

        if prompt is None:
            self.session.run_id = run_id
            self.query_one(RunStatusBar).set_run_id(run_id)
            self._append(
                Text(
                    f"Attached to run {run_id[:8]} · next prompt continues it",
                    style="dim",
                )
            )
            return

        self.session.run_id = run_id
        self._start_run(prompt, run_id=run_id)

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

    @work(thread=False, exit_on_error=False)
    async def _do_run(self, task: str, run_id: str | None) -> None:
        """Thin worker: delegate to Session; UI via TuiPresentationHandler."""
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

    @work(thread=False, exit_on_error=False)
    async def _do_approve(self, run_id: str, verdict: ApprovalVerdict) -> None:
        """Thin worker: delegate to Session; UI via TuiPresentationHandler."""
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
        self.exit()

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
