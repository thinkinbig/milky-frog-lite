from __future__ import annotations

from datetime import datetime
from pathlib import Path

from rich.console import Console, RenderableType
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from milky_frog.checkpoint import StoredRun
from milky_frog.diagnostics import CheckStatus, Diagnostic
from milky_frog.domain import MessageRole, RunState, RunStatus

console = Console()
error_console = Console(stderr=True)

_RUN_STATUS_STYLES = {
    RunStatus.RUNNING: "bold cyan",
    RunStatus.WAITING_FOR_INPUT: "bold yellow",
    RunStatus.WAITING_FOR_APPROVAL: "bold yellow",
    RunStatus.PAUSED_LIMIT: "bold yellow",
    RunStatus.COMPLETED: "bold green",
    RunStatus.FAILED: "bold red",
    RunStatus.CANCELLED: "dim",
}


def _local_time(value: datetime) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _assistant_preview(state: RunState) -> str | None:
    for message in reversed(state.messages):
        if message.role is MessageRole.ASSISTANT and message.content:
            preview = message.content.replace("\n", " ")
            return preview[:120] + ("…" if len(preview) > 120 else "")
    return None


def _status_tag(status: RunStatus) -> Text:
    """Render a coloured status label."""
    style = _RUN_STATUS_STYLES.get(status, "dim")
    return Text(status.value, style=style)


def render_diagnostics(diagnostics: tuple[Diagnostic, ...]) -> None:
    check_styles = {
        CheckStatus.PASS: "bold green",
        CheckStatus.WARN: "bold yellow",
        CheckStatus.FAIL: "bold red",
    }
    table = Table(
        title="Milky Frog doctor",
        title_style="bold",
        header_style="bold",
        border_style="bright_black",
        show_edge=True,
    )
    table.add_column("Status", no_wrap=True)
    table.add_column("Check")
    table.add_column("Value")
    for diagnostic in diagnostics:
        style = check_styles[diagnostic.status]
        status = Text(diagnostic.status, style=style)
        # Diagnostic.name/value are data, not markup: a value containing "[sandbox]"
        # must render literally instead of being parsed as a Rich style tag.
        table.add_row(status, escape(diagnostic.name), escape(diagnostic.value))
    console.print()
    console.print(table)

    failed = sum(item.status is CheckStatus.FAIL for item in diagnostics)
    warned = sum(item.status is CheckStatus.WARN for item in diagnostics)
    if failed:
        console.print(
            Text(
                f"\nDoctor found {failed} failure(s) and {warned} warning(s).",
                style="red",
            )
        )
    elif warned:
        console.print(Text(f"\nDoctor passed with {warned} warning(s).", style="yellow"))
    else:
        console.print(Text("\nDoctor passed.", style="green"))


def render_error(message: str, *, hint: str | None = None) -> None:
    error = Text.assemble(
        ("error: ", "bold red"),
        (message, "bold"),
    )
    error_console.print()
    error_console.print(error)
    if hint:
        help_text = Text.assemble(
            ("hint: ", "bold cyan"),
            (hint, "cyan"),
        )
        error_console.print(help_text)


def render_initialized(root: Path, *, already_exists: bool = False) -> None:
    if already_exists:
        message = Text.assemble(
            ("[info] ", "yellow"),
            ("Already initialized: ", "yellow"),
            (str(root), "bold"),
        )
    else:
        message = Text.assemble(
            ("Initialized: ", "green"),
            (str(root), "bold green"),
        )
    console.print()
    console.print(message)


def runs_table(runs: tuple[StoredRun, ...]) -> RenderableType:
    """Build a Rich table of recent Runs for CLI or TUI output."""
    if not runs:
        return Text.assemble(
            ("No runs yet.\n", ""),
            ("   Type a task to start one, or use /resume to attach.", "dim"),
        )

    table = Table(
        title_style="bold",
        header_style="bold",
        border_style="bright_black",
        show_edge=True,
        expand=True,
    )
    table.add_column("Run", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Workspace", overflow="fold")
    table.add_column("Updated", no_wrap=True)
    for run in runs:
        table.add_row(
            run.run_id[:8],
            _status_tag(run.status),
            str(run.workspace),
            _local_time(run.updated_at),
        )
    return table


def render_runs(runs: tuple[StoredRun, ...]) -> None:
    console.print()
    console.print(Panel(runs_table(runs), title="Recent runs", border_style="bright_black"))


def render_run(run: StoredRun, state: RunState) -> None:
    # ── Summary panel ──
    summary = Table.grid(padding=(1, 2))
    summary.add_column(style="bold yellow", no_wrap=True)
    summary.add_column(overflow="fold")
    summary.add_row("Run", f"[bold]{run.run_id[:24]}[/bold]")
    summary.add_row("Status", _status_tag(run.status))
    summary.add_row("Workspace", str(run.workspace))
    summary.add_row("Created", _local_time(run.created_at))
    summary.add_row("Updated", _local_time(run.updated_at))
    summary.add_row("Model calls", str(state.completed_model_calls))
    summary.add_row("Messages", str(len(state.messages)))
    if run.final_message:
        summary.add_row("Final message", run.final_message)
    preview = _assistant_preview(state)
    if preview:
        summary.add_row("Last assistant", preview)

    # ── Message transcript ──
    transcript = Table(
        title="Transcript",
        title_style="bold",
        header_style="bold",
        border_style="bright_black",
        show_edge=True,
    )
    transcript.add_column("#", justify="right", no_wrap=True, style="dim")
    transcript.add_column("Role", no_wrap=True)
    transcript.add_column("Content", overflow="fold")

    role_styles = {
        MessageRole.SYSTEM: "dim",
        MessageRole.USER: "bold cyan",
        MessageRole.ASSISTANT: "bold yellow",
        MessageRole.TOOL: "green",
    }
    for index, message in enumerate(state.messages, start=1):
        style = role_styles.get(message.role, "dim")
        role_text = Text(message.role.value, style=style)
        content = message.content or ("tool calls" if message.tool_calls else "—")
        transcript.add_row(str(index), role_text, content)

    console.print()
    console.print(Panel(summary, title="Run summary", border_style="yellow", expand=False))
    console.print()
    console.print(transcript)
    console.print()
