from __future__ import annotations

from datetime import datetime

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from milky_frog.checkpoint import RunEvent, StoredRun
from milky_frog.domain import RunStatus
from milky_frog.ui.console import get_box_width
from milky_frog.ui.presenter._base import _Surface

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


class _RunsSurface(_Surface):
    def runs(self, runs: tuple[StoredRun, ...]) -> None:
        if not runs:
            self.out.print("No runs yet.")
            self.out.print(Text("Start one with: milky-frog run TASK", style="dim"))
            return

        table = Table(title="Recent runs", header_style="bold")
        table.add_column("Run", no_wrap=True)
        table.add_column("Status", no_wrap=True)
        table.add_column("Workspace", overflow="fold")
        table.add_column("Updated", no_wrap=True)
        for run in runs:
            status = Text(run.status, style=_RUN_STYLES[run.status])
            table.add_row(run.run_id, status, str(run.workspace), _local_time(run.updated_at))
        self.out.print(table)

    def run(self, run: StoredRun, events: tuple[RunEvent, ...]) -> None:
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
            body = Group(
                Panel(summary, title="Run summary", expand=False, width=get_box_width()),
                event_table,
            )
        else:
            body = Group(
                Panel(summary, title="Run summary", expand=False, width=get_box_width()),
                Text("No checkpoint events.", style="dim"),
            )
        self.out.print(body)
