from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, override

from rich.console import RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Footer, Header, Input, Static
from textual.worker import Worker

from milky_frog.app.session import AgentSession
from milky_frog.core.controller import RunController
from milky_frog.domain import ApprovalDecision, ApprovalVerdict, ResumeError, RunStatus, RunUsage
from milky_frog.events.emitter import format_approval_message
from milky_frog.project import load_project_config
from milky_frog.tui.cli import runs_table
from milky_frog.tui.logo import welcome_banner
from milky_frog.tui.messages import (
    AddText,
    AddThinking,
    ApprovalOptionSelected,
    ApprovalRequired,
    BashOutputMsg,
    CompactionMsg,
    GitOutputMsg,
    GrepOutputMsg,
    McpOptionSelected,
    McpReloadRequested,
    PendingApproval,
    RunError,
    RunFinished,
    RunNoticeMsg,
    SkillOptionSelected,
    ToolCallMsg,
    ToolResultMsg,
    UpdateUsage,
)
from milky_frog.tui.render_helpers import conversation_row
from milky_frog.tui.rendering import (
    COMMANDS,
    complete_command,
    matching_commands,
)
from milky_frog.tui.viewmodels.approval_vm import ApprovalViewModel
from milky_frog.tui.viewmodels.conversation_vm import ConversationViewModel
from milky_frog.tui.viewmodels.mcp_vm import McpViewModel
from milky_frog.tui.viewmodels.skills_vm import SkillsViewModel
from milky_frog.tui.widgets.prompt import PromptInput
from milky_frog.tui.widgets.status_bar import RunStatusBar


@dataclass(frozen=True, slots=True)
class TuiLaunch:
    """Optional startup action when the TUI opens from a Typer command."""

    run_id: str | None = None
    prompt: str | None = None
    advance_pending: bool = False


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

    .skill-picker {
        height: auto;
        margin-bottom: 1;
        border: round cyan;
        padding: 0 1 1 0;
    }

    .skill-picker SelectionList {
        height: auto;
        max-height: 12;
        margin: 0 1;
        background: transparent;
        border: none;
        padding: 0;
    }

    .skill-picker SelectionList > .option-list--option-highlighted {
        background: $accent 20%;
        color: $text;
    }

    .mcp-picker {
        height: auto;
        margin-bottom: 1;
        border: round magenta;
        padding: 0 1 1 0;
    }

    .mcp-picker SelectionList {
        height: auto;
        max-height: 12;
        margin: 0 1;
        background: transparent;
        border: none;
        padding: 0;
    }

    .mcp-picker SelectionList > .option-list--option-highlighted {
        background: $accent 20%;
        color: $text;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "request_exit", "Exit"),
        Binding("ctrl+d", "request_exit", "Exit"),
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

        # ViewModels
        self._conv = ConversationViewModel(self)
        self._approval = ApprovalViewModel(self)
        self._skills = SkillsViewModel(self)
        self._mcp = McpViewModel(self)

    @property
    def session(self) -> AgentSession:
        return self._session

    @property
    def run_controller(self) -> RunController:
        return self._run_controller

    # ── Compose ────────────────────────────────────────────────────

    @override
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Vertical(
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

    # ── Conversation plumbing / DOM ────────────────────────────────

    def _conversation(self) -> VerticalScroll:
        return self.query_one("#conversation", VerticalScroll)

    def _append(self, renderable: RenderableType, *, spaced: bool = True) -> Static:
        widget = Static(renderable)
        if spaced:
            widget.add_class("spaced")
        self._conversation().mount(widget)
        self._scroll_end()
        return widget

    def _scroll_end(self) -> None:
        self.call_after_refresh(self._conversation().scroll_end, animate=False)

    # ── Welcome / Help / Runs ──────────────────────────────────────

    def _render_welcome(self) -> None:
        self._append(welcome_banner())
        self._append(Text("Welcome to MILKY FROG · 奶蛙", style="bold yellow"))
        self._append(
            Text.assemble(
                ("Describe what to build, fix, or explain — be specific\n", "dim"),
                ("/help lists commands · /clear resets · /exit leaves", "dim"),
            )
        )

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

    # ── Message handlers (lifecycle → ViewModel delegation) ────────

    def on_add_thinking(self, event: AddThinking) -> None:
        self._conv.on_thinking(event.text)

    def on_add_text(self, event: AddText) -> None:
        self._conv.on_text(event.text)

    def on_tool_call_msg(self, event: ToolCallMsg) -> None:
        self._conv.on_tool_call(event.call_id, event.name, event.arguments)

    def on_tool_result_msg(self, event: ToolResultMsg) -> None:
        self._conv.render_tool_result(
            event.call_id, event.name, event.content, is_error=event.is_error
        )

    def on_git_output_msg(self, event: GitOutputMsg) -> None:
        self._conv.finalize_tool_call(event.call_id, is_error=event.is_error)
        self._conv.render_command_output(event.content, is_error=event.is_error)

    def on_grep_output_msg(self, event: GrepOutputMsg) -> None:
        self._conv.finalize_tool_call(event.call_id, is_error=event.is_error)
        self._conv.render_command_output(event.content, is_error=event.is_error)

    def on_bash_output_msg(self, event: BashOutputMsg) -> None:
        self._conv.finalize_tool_call(event.call_id, is_error=event.is_error)
        self._conv.render_command_output(event.content, is_error=event.is_error)

    def on_update_usage(self, event: UpdateUsage) -> None:
        self.query_one(RunStatusBar).set_usage(event.usage)

    def _clear_worker(self) -> None:
        """Clean up worker reference and notify session."""
        self._worker = None
        self.session.attach_worker(None)

    def _enable_input_focus(self) -> None:
        """Enable prompt input and focus it."""
        self.query_one("#prompt-input", Input).disabled = False
        self.query_one("#prompt-input", Input).focus()

    def on_run_finished(self, event: RunFinished) -> None:
        self._clear_worker()
        self._conv.finish()

        status_bar = self.query_one(RunStatusBar)

        if event.status is RunStatus.COMPLETED:
            self._conv.render_assistant_footer(event.result.run_id, usage=event.result.usage)
        elif event.status is RunStatus.CANCELLED:
            self._conv.render_error(event.message, hint="Type a new prompt, or Ctrl+C to exit.")
        elif event.status is RunStatus.FAILED or event.status is RunStatus.PAUSED_LIMIT:
            self._conv.render_error(event.message)

        status_bar.set_run_id(event.result.run_id)
        status_bar.set_usage(event.result.usage or RunUsage())
        status_bar.set_ready()
        self._enable_input_focus()

    def on_run_error(self, event: RunError) -> None:
        self._clear_worker()
        self._conv.close_phase()
        self._conv.render_error(event.error)
        self.query_one(RunStatusBar).set_ready()
        self._enable_input_focus()

    def on_compaction_msg(self, event: CompactionMsg) -> None:
        self._conv.finish_compaction(event.messages_folded)

    def on_run_notice_msg(self, event: RunNoticeMsg) -> None:
        self._conv.render_notification(event.message, event.level)

    # ── Approval (delegated to ApprovalViewModel) ─────────────────

    def on_approval_required(self, event: ApprovalRequired) -> None:
        self._approval.begin(event)

    def on_approval_option_selected(self, event: ApprovalOptionSelected) -> None:
        self._approval.handle_option(event.action)

    # ── Skill picker (delegated to SkillsViewModel) ───────────────

    def on_skill_option_selected(self, event: SkillOptionSelected) -> None:
        self._skills.on_picker_confirmed(event.selected)

    # ── MCP picker (delegated to McpViewModel) ────────────────────

    def on_mcp_option_selected(self, event: McpOptionSelected) -> None:
        self._mcp.on_picker_confirmed(event.enabled)

    def on_mcp_reload_requested(self, _event: McpReloadRequested) -> None:
        self._do_reload_mcp()

    @work(thread=False, exit_on_error=False)
    async def _do_reload_mcp(self) -> None:
        try:
            count = await self.session.reload_mcp()
        except Exception as exc:
            self._append(Text(f"MCP reload failed: {exc}", style="bold red"))
        else:
            label = "tool" if count == 1 else "tools"
            self._append(Text(f"MCP ready — {count} {label} active.", style="green"))

    # ── Input handling ────────────────────────────────────────────

    def on_input_changed(self, message: Input.Changed) -> None:
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
        task = message.value.strip()
        if not task:
            return

        if task.startswith("/") and " " not in task:
            completed = complete_command(task)
            if completed is not None and completed != task:
                task = completed

        prompt_input = self.query_one("#prompt-input", PromptInput)
        prompt_input.remember(task)
        prompt_input.clear()
        self.query_one("#command-hints", Static).display = False

        # Pending approval?
        if self._approval.is_pending:
            if self._approval.deny_reason_mode or not self._approval.has_menu:
                self._approval.handle_text_input(task)
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

        if command.startswith("/skill"):
            self._skills.handle_command(task)
            return

        if command == "/mcp":
            self._mcp.handle_command()
            return

        if command == "/compact":
            self._handle_compact()
            return

        self._start_run(task, run_id=self.session.run_id)

    def _handle_resume(self, task: str) -> None:
        parsed = self.run_controller.parse_resume_command(task)
        if isinstance(parsed, str):
            hint = None
            if parsed == "No runs found to resume.":
                hint = "Start a new task to create a run first."
            self._conv.render_error(parsed, hint=hint)
            return
        self._attach_or_continue_run(
            parsed.run_id,
            prompt=parsed.prompt,
            advance_pending=False,
        )

    def _handle_compact(self) -> None:
        """Force-compact the current Run's transcript."""
        run_id = self.session.run_id
        if run_id is None:
            self._conv.render_error("No active run to compact.", hint="Start a task first.")
            return

        self.session.busy = True
        self.query_one(RunStatusBar).set_working()
        self.query_one("#prompt-input", PromptInput).disabled = True

        self._worker = self._do_compact(run_id)
        self.session.attach_worker(self._worker.cancel)

    def _attach_or_continue_run(
        self,
        run_id: str,
        *,
        prompt: str | None = None,
        advance_pending: bool = False,
    ) -> None:
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
            approvals = tuple(
                PendingApproval(call.id, call.name, format_approval_message(call))
                for call in outcome.pending_calls
            )
            self.post_message(ApprovalRequired(outcome.run_id, approvals))
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

    # ── Run workers ───────────────────────────────────────────────

    def _start_run(self, task: str, *, run_id: str | None = None) -> None:
        self.session.busy = True
        self._conv.close_phase()

        self._conv.render_user(task)
        self.query_one(RunStatusBar).set_working()
        self.query_one("#prompt-input", PromptInput).disabled = True

        self._worker = self._do_run(task, run_id)
        self.session.attach_worker(self._worker.cancel)

    def _start_continue_pending(self, run_id: str) -> None:
        self.session.run_id = run_id
        self.session.busy = True
        self._conv.close_phase()

        self.query_one(RunStatusBar).set_run_id(run_id)
        self.query_one(RunStatusBar).set_working()
        self.query_one("#prompt-input", PromptInput).disabled = True

        self._worker = self._do_continue(run_id)
        self.session.attach_worker(self._worker.cancel)

    def _start_approvals(self, run_id: str, verdicts: dict[str, ApprovalVerdict]) -> None:
        self.session.busy = True
        self._conv.close_phase()

        approved = sum(
            verdict.decision is ApprovalDecision.APPROVE for verdict in verdicts.values()
        )
        denied = len(verdicts) - approved
        summary = f"Approval batch complete · {approved} approved"
        if denied:
            summary += f", {denied} denied"
        self._append(conversation_row(Text("▸", style="bold cyan"), Text(summary, style="dim")))
        self.query_one(RunStatusBar).set_working()
        self.query_one("#prompt-input", PromptInput).disabled = True

        self._worker = self._do_approve(run_id, verdicts)
        self.session.attach_worker(self._worker.cancel)

    @work(thread=False, exit_on_error=False)
    async def _do_continue(self, run_id: str) -> None:
        try:
            selected = tuple(sorted(self._skills.active)) if self._skills.touched else None
            await self.session.continue_with(run_id, selected_skills=selected)
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
    async def _do_compact(self, run_id: str) -> None:
        """Worker: force compaction, animating a spinner during the model call.

        ``session.compact`` emits ``run_compaction`` on the hub when it actually
        folds anything, which drives ``on_compaction_msg`` → ``finish_compaction``
        (settling the spinner and billing the usage). When nothing is compacted we
        settle the spinner ourselves.
        """
        self._conv.start_compaction()
        try:
            summary = await self.session.compact(run_id)
        except ValueError as error:
            self._conv.cancel_compaction("compaction failed")
            self.post_message(RunError(str(error)))
        else:
            if not summary:
                self._conv.cancel_compaction("transcript fits within budget; nothing to summarise")
        finally:
            self._clear_worker()
            self.session.busy = False
            self.query_one(RunStatusBar).set_ready()
            self._enable_input_focus()

    @work(thread=False, exit_on_error=False)
    async def _do_run(self, task: str, run_id: str | None) -> None:
        try:
            if run_id is None:
                selected = tuple(sorted(self._skills.active))
                await self.session.start_new(task, selected_skills=selected)
            else:
                selected = tuple(sorted(self._skills.active)) if self._skills.touched else None
                await self.session.continue_with(run_id, prompt=task, selected_skills=selected)
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

    @work(thread=False, exit_on_error=False)
    async def _do_approve(self, run_id: str, verdicts: dict[str, ApprovalVerdict]) -> None:
        try:
            await self.session.respond_approvals(run_id, verdicts)
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

    # ── Key bindings ──────────────────────────────────────────────

    def action_request_exit(self) -> None:
        self._request_exit()

    def _request_exit(self) -> None:
        self._prepare_shutdown()
        self.exit()

    def _prepare_shutdown(self) -> None:
        self.session.request_shutdown()

    def action_cancel_run(self) -> None:
        if self._skills.has_picker:
            self._skills.dismiss_picker()
            return
        if self._mcp.has_picker:
            self._mcp.dismiss_picker()
            return
        if not self.session.busy:
            return
        self.query_one(RunStatusBar).set_cancelling()
        self.session.cancel()
        if self._worker is not None:
            self._worker.cancel()
