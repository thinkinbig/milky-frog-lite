from __future__ import annotations

from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from milky_frog.checkpoint import StoredRun
from milky_frog.diagnostics import CheckStatus, Diagnostic
from milky_frog.domain import MessageRole, RunResult, RunState, RunStatus
from milky_frog.ui.usage import format_run_usage

console = Console()
error_console = Console(stderr=True)

_RUN_STYLES = {
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


def render_diagnostics(diagnostics: tuple[Diagnostic, ...]) -> None:
    _CHECK_STYLES = {
        CheckStatus.PASS: "bold green",
        CheckStatus.WARN: "bold yellow",
        CheckStatus.FAIL: "bold red",
    }
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


def render_error(message: str, *, hint: str | None = None) -> None:
    error = Text("Error: ", style="bold red")
    error.append(message)
    error_console.print(error)
    if hint:
        help_text = Text("Hint: ", style="bold cyan")
        help_text.append(hint)
        error_console.print(help_text)


def render_initialized(root: Path, *, already_exists: bool = False) -> None:
    if already_exists:
        message = Text("Already initialized: ", style="yellow")
    else:
        message = Text("Initialized: ", style="green")
    message.append(str(root))
    console.print(message)


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


def render_run(run: StoredRun, state: RunState) -> None:
    from rich.console import Group

    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="bold")
    summary.add_column(overflow="fold")
    summary.add_row("Run", run.run_id)
    summary.add_row("Status", Text(run.status, style=_RUN_STYLES[run.status]))
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

    transcript = Table(title="Transcript", header_style="bold")
    transcript.add_column("#", justify="right", no_wrap=True)
    transcript.add_column("Role", no_wrap=True)
    transcript.add_column("Content", overflow="fold")
    for index, message in enumerate(state.messages, start=1):
        content = message.content or ("tool calls" if message.tool_calls else "—")
        transcript.add_row(str(index), message.role.value, content)

    console.print(Group(Panel(summary, title="Run summary", expand=False), transcript))


def render_result(result: RunResult) -> None:
    """Render a one-shot RunResult (for ``run`` and ``resume`` CLI commands)."""
    from rich.markdown import Markdown

    if result.final_message:
        body = Markdown(result.final_message)
        console.print(body)
    footer = f"  ⎿ run {result.run_id[:8]}"
    usage_str = format_run_usage(result.usage) if result.usage is not None else None
    if usage_str is not None:
        footer = f"{footer} · {usage_str}"
    console.print(Text(footer, style="bright_black"))
