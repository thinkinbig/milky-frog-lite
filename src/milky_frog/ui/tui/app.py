from __future__ import annotations

import asyncio
from pathlib import Path
from typing import ClassVar

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
from textual.widgets import Footer, Header, Input, Static
from textual.worker import Worker

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.domain import RunRequest, RunResult, RunStatus, RunUsage
from milky_frog.harness.runner import Harness
from milky_frog.harness.tools import ToolRegistry, default_tools
from milky_frog.models import OpenAIModel
from milky_frog.project import load_project_config
from milky_frog.settings import Settings
from milky_frog.ui.logo import pixel_frog_logo
from milky_frog.ui.tui.handlers import TuiStreamingHandlers
from milky_frog.ui.tui.messages import (
    AddText,
    AddThinking,
    RunError,
    RunFinished,
    ToolCallMsg,
    ToolResultMsg,
    UpdateUsage,
)
from milky_frog.ui.tui.rendering import (
    COMMANDS,
    complete_command,
    format_tool_call,
    matching_commands,
    summarize_tool_result,
)
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


def _thinking(text: str) -> Text:
    """Render the reasoning block: a ``✻ thinking`` header with the body below."""
    if not text:
        return Text("✻ thinking", style="dim italic")
    return Text.assemble(("✻ thinking\n", "dim italic"), (text, "dim italic"))


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

    def watch_status_text(self, value: str) -> None:
        self.update(self._format(value))

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
        parts.append(Text(state, style="green" if state == "ready" else "yellow"))
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


class MilkyFrogApp(App[None]):
    """Textual TUI for Milky Frog local coding agent.

    Layout (top to bottom)::

        Header        — model, workspace, clock
        VerticalScroll — conversation: one widget per message, so text reflows
                         to the terminal width on resize (unlike an append-only log)
        command hints — slash-command completions (hidden unless typing a command)
        PromptInput   — text prompt with Tab completion and Up/Down history
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
        Binding("escape", "cancel_run", "Interrupt"),
    ]

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings
        self._run_id: str | None = None
        self._busy = False
        self._harness: Harness | None = None
        self._checkpoints: SqliteCheckpointStore | None = None
        self._worker: Worker[None] | None = None

        # Streaming state: buffers plus the live widget being updated in place.
        self._thinking_buf: list[str] = []
        self._answer_buf: list[str] = []
        self._phase: str | None = None  # None | "thinking" | "answer"
        self._thinking_widget: Static | None = None
        self._answer_widget: Static | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Vertical(
            # Not focusable: the prompt must keep focus so typing (incl. IME
            # composition) always lands in the input; mouse-wheel scroll still works.
            VerticalScroll(id="conversation", can_focus=False),
            Static(id="command-hints"),
            PromptInput(id="prompt-input", placeholder="Type a task and press Enter..."),
            RunStatusBar(
                model=self._settings.model or "unknown",
                workspace=Path.cwd(),
            ),
        )
        yield Footer()

    def on_mount(self) -> None:
        """App started: render welcome and assemble Harness infrastructure."""
        self._checkpoints = SqliteCheckpointStore(self._settings.database_path)

        from milky_frog.handlers import LifecycleBus

        model = OpenAIModel(
            api_key=self._settings.api_key or "",
            model=self._settings.model or "",
            base_url=self._settings.base_url,
        )
        bus = LifecycleBus()
        TuiStreamingHandlers(self).register(bus)

        self._harness = Harness(
            model=model,
            tools=ToolRegistry(default_tools()),
            checkpoints=self._checkpoints,
            handlers=bus,
        )

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

    def _flush_thinking(self) -> None:
        """Finalize the live reasoning widget (it already holds the streamed body)."""
        widget = self._thinking_widget
        self._thinking_widget = None
        has_text = bool("".join(self._thinking_buf).strip())
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
            self._thinking_widget = self._append(_thinking(""))
        self._thinking_buf.append(event.text)
        if self._thinking_widget is not None:
            self._thinking_widget.update(_thinking("".join(self._thinking_buf).strip()))
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
        """Close the open assistant block, then write the tool call signature."""
        self._close_phase()
        signature = format_tool_call(event.name, event.arguments)
        self._append(
            Text.assemble(("  ⏺ ", "bold cyan"), (signature, "cyan")),
            spaced=False,
        )

    def on_tool_result_msg(self, event: ToolResultMsg) -> None:
        """Write the tool result as an indented summary line under its call."""
        summary = summarize_tool_result(event.content, is_error=event.is_error)
        mark, style = ("✗", "red") if event.is_error else ("⎿", "bright_black")
        self._append(Text.assemble((f"    {mark} ", style), (summary, "dim")))

    def on_update_usage(self, event: UpdateUsage) -> None:
        status_bar = self.query_one(RunStatusBar)
        status_bar.set_usage(event.usage)

    def on_run_finished(self, event: RunFinished) -> None:
        """Run finished: commit any buffered content and show the footer."""
        self._busy = False
        self._worker = None
        self._close_phase()

        status_bar = self.query_one(RunStatusBar)

        if event.status is RunStatus.COMPLETED:
            self._render_assistant_footer(event.result.run_id, usage=event.result.usage)
        elif event.status is RunStatus.CANCELLED:
            self._render_error(event.message, hint="Type a new prompt, or Ctrl+C to exit.")
        elif event.status is RunStatus.FAILED or event.status in (
            RunStatus.PAUSED_LIMIT,
            RunStatus.WAITING_FOR_APPROVAL,
        ):
            self._render_error(event.message)

        # Thread the Run ID so the next prompt continues this conversation.
        self._run_id = event.result.run_id
        status_bar.set_run_id(self._run_id)
        status_bar.set_usage(event.result.usage or RunUsage())
        status_bar.set_ready()
        self.query_one("#prompt-input", Input).disabled = False
        self.query_one("#prompt-input", Input).focus()

    def on_run_error(self, event: RunError) -> None:
        self._busy = False
        self._worker = None
        self._close_phase()
        self._render_error(event.error)
        status_bar = self.query_one(RunStatusBar)
        status_bar.set_ready()
        self.query_one("#prompt-input", Input).disabled = False
        self.query_one("#prompt-input", Input).focus()

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
        if self._busy:
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
            self._run_id = None
            return

        if command.startswith("/resume"):
            self._handle_resume(task)
            return

        self._start_run(task)

    def _handle_resume(self, task: str) -> None:
        """Parse ``/resume`` and either attach to a Run or continue it."""
        rest = task[len("/resume") :].strip()
        if not rest:
            self._render_error(
                "Usage: /resume RUN_ID [prompt]",
                hint="List available Runs with: milky-frog runs",
            )
            return
        head, _, tail = rest.partition(" ")
        run_id = head.strip()
        prompt = tail.strip() or None

        try:
            if self._checkpoints is not None:
                run_id = self._checkpoints.resolve_run_id(run_id)
        except (LookupError, ValueError) as error:
            self._render_error(f"unknown Run: {error}")
            return

        if prompt is None:
            self._run_id = run_id
            status_bar = self.query_one(RunStatusBar)
            status_bar.set_run_id(run_id)
            self._append(
                Text(f"Attached to run {run_id[:8]} · next prompt continues it", style="dim")
            )
            return

        self._run_id = run_id
        self._start_run(prompt, run_id=run_id)

    def _start_run(self, task: str, *, run_id: str | None = None) -> None:
        """Kick off a Run as a Textual worker."""
        self._busy = True
        self._phase = None
        self._thinking_buf.clear()
        self._answer_buf.clear()
        self._thinking_widget = None
        self._answer_widget = None

        self._render_user_message(task)

        status_bar = self.query_one(RunStatusBar)
        status_bar.set_working()

        prompt_input = self.query_one("#prompt-input", PromptInput)
        prompt_input.disabled = True

        self._worker = self._do_run(task, run_id)

    @work(thread=False, exit_on_error=False)
    async def _do_run(self, task: str, run_id: str | None) -> None:
        """Run the Harness as an async worker inside Textual's event loop."""
        harness = self._harness
        if harness is None:
            self.post_message(RunError("Harness not initialised"))
            return

        try:
            if run_id is None:
                config = load_project_config(Path.cwd())
                result = await harness.run(
                    RunRequest(task, Path.cwd(), max_model_calls=config.max_model_calls)
                )
            else:
                if self._checkpoints is None:
                    self.post_message(RunError("Checkpoint store not initialised"))
                    return
                stored = self._checkpoints.get_run(run_id)
                if stored is None:
                    self.post_message(RunError(f"unknown Run: {run_id}"))
                    return
                config = load_project_config(stored.workspace)
                result = await harness.resume(
                    run_id,
                    max_model_calls=config.max_model_calls,
                    prompt=task,
                )

            self.post_message(
                RunFinished(
                    result=result,
                    status=result.status,
                    message=result.final_message,
                    is_streamed=self._phase == "answer",
                )
            )
        except asyncio.CancelledError:
            self.post_message(
                RunFinished(
                    result=RunResult(run_id or "unknown", RunStatus.CANCELLED, "cancelled", 0),
                    status=RunStatus.CANCELLED,
                    message="Cancelled the current task.",
                    is_streamed=False,
                )
            )
        except Exception as error:
            self.post_message(RunError(f"{type(error).__name__}: {error}"))

    # ── Key bindings ──────────────────────────────────────────────────

    def action_request_exit(self) -> None:
        self.exit()

    def action_cancel_run(self) -> None:
        """Interrupt the in-flight Run, if any (Esc)."""
        if self._busy and self._worker is not None:
            self._worker.cancel()
