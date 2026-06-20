from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from rich.console import Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from milky_frog.checkpoint import RunEvent, StoredRun
from milky_frog.domain import RunStatus
from milky_frog.ui.console import console, error_console
from milky_frog.ui.logo import pixel_frog_logo


class CheckStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    name: str
    status: CheckStatus
    value: str


_CHECK_STYLES = {
    CheckStatus.PASS: "bold green",
    CheckStatus.WARN: "bold yellow",
    CheckStatus.FAIL: "bold red",
}

_RUN_STYLES = {
    RunStatus.RUNNING: "bold cyan",
    RunStatus.WAITING_FOR_INPUT: "bold yellow",
    RunStatus.WAITING_FOR_APPROVAL: "bold yellow",
    RunStatus.PAUSED_LIMIT: "bold yellow",
    RunStatus.COMPLETED: "bold green",
    RunStatus.FAILED: "bold red",
    RunStatus.CANCELLED: "dim",
}


def render_diagnostics(diagnostics: tuple[Diagnostic, ...]) -> None:
    table = Table(title="Milky Frog doctor", header_style="bold")
    table.add_column("Status", no_wrap=True)
    table.add_column("Check")
    table.add_column("Value")
    for diagnostic in diagnostics:
        status = Text(diagnostic.status, style=_CHECK_STYLES[diagnostic.status])
        table.add_row(status, diagnostic.name, diagnostic.value)
    console.print(table)

    failed = sum(item.status is CheckStatus.FAIL for item in diagnostics)
    warned = sum(item.status is CheckStatus.WARN for item in diagnostics)
    if failed:
        console.print(
            Text(f"Doctor found {failed} failure(s) and {warned} warning(s).", style="red")
        )
    elif warned:
        console.print(Text(f"Doctor passed with {warned} warning(s).", style="yellow"))
    else:
        console.print(Text("Doctor passed.", style="green"))


def render_initialized(root: Path, *, already_exists: bool = False) -> None:
    if already_exists:
        message = Text("Already initialized: ", style="yellow")
    else:
        message = Text("Initialized: ", style="green")
    message.append(str(root))
    console.print(message)


def render_interactive_welcome(*, model: str, workspace: Path) -> None:
    details = Table.grid(padding=(0, 1))
    details.add_column()
    title = Text("Welcome to MILKY FROG", style="bold yellow")
    title.append(" · 奶蛙", style="bold white")
    details.add_row(title)
    details.add_row(Text("Local coding agent", style="dim"))
    details.add_row("")
    details.add_row(Text(f"model      {model}"))
    details.add_row(Text(f"workspace  {workspace}", overflow="fold"))
    details.add_row("")
    details.add_row(Text("/help for commands", style="dim"))

    welcome = Table.grid(padding=(0, 3))
    welcome.add_column(no_wrap=True)
    welcome.add_column(overflow="fold")
    welcome.add_row(pixel_frog_logo(), details)

    console.print(
        Panel(
            welcome,
            border_style="bright_black",
            padding=(1, 1),
            expand=False,
        )
    )
    console.print(Text("What should Milky Frog build?", style="bold"))
    console.print()


def render_assistant(message: str, *, run_id: str | None = None) -> None:
    body = Markdown(message) if message else Text("No response content.", style="dim")
    response = Table.grid(padding=(0, 1))
    response.add_column(no_wrap=True)
    response.add_column(ratio=1)
    response.add_row(Text("●", style="bold yellow"), body)
    console.print(response)
    if run_id:
        console.print(Text(f"  run {run_id[:8]}", style="dim"))


def render_interactive_help() -> None:
    commands = Table.grid(padding=(0, 2))
    commands.add_column(style="yellow", no_wrap=True)
    commands.add_column(style="dim")
    commands.add_row("/help", "Show available commands")
    commands.add_row("/clear", "Clear the terminal")
    commands.add_row("/exit", "Leave Milky Frog")
    commands.add_row("exit · quit", "Leave Milky Frog")
    console.print(Panel(commands, title="Commands", border_style="bright_black", expand=False))


def render_runs(runs: tuple[StoredRun, ...]) -> None:
    if not runs:
        console.print("No runs yet.")
        console.print(Text("Start one with: milky-frog run TASK", style="dim"))
        return

    table = Table(title="Recent runs", header_style="bold")
    table.add_column("Run", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Workspace", overflow="fold")
    table.add_column("Updated", no_wrap=True)
    for run in runs:
        status = Text(run.status, style=_RUN_STYLES[run.status])
        table.add_row(run.run_id, status, str(run.workspace), _local_time(run.updated_at))
    console.print(table)


def render_run(run: StoredRun, events: tuple[RunEvent, ...]) -> None:
    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="bold")
    summary.add_column(overflow="fold")
    summary.add_row("Run", run.run_id)
    summary.add_row("Status", Text(run.status, style=_RUN_STYLES[run.status]))
    summary.add_row("Workspace", str(run.workspace))
    summary.add_row("Created", _local_time(run.created_at))
    summary.add_row("Updated", _local_time(run.updated_at))

    if events:
        event_table = Table(title="Checkpoint events", header_style="bold")
        event_table.add_column("Sequence", justify="right", no_wrap=True)
        event_table.add_column("Event")
        for event in events:
            sequence = "—" if event.sequence is None else str(event.sequence)
            event_table.add_row(sequence, event.event_type)
        body = Group(Panel(summary, title="Run summary", expand=False), event_table)
    else:
        body = Group(
            Panel(summary, title="Run summary", expand=False),
            Text("No checkpoint events.", style="dim"),
        )
    console.print(body)


def render_error(message: str, *, hint: str | None = None) -> None:
    error = Text("Error: ", style="bold red")
    error.append(message)
    error_console.print(error)
    if hint:
        help_text = Text("Hint: ", style="bold cyan")
        help_text.append(hint)
        error_console.print(help_text)


def _local_time(value: datetime) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
