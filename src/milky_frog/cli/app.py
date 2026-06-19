from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from milky_frog import __version__
from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.settings import Settings
from milky_frog.ui import console

app = typer.Typer(no_args_is_help=True, help="A lightweight local coding agent.")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option("--version", callback=_version_callback, is_eager=True),
    ] = None,
) -> None:
    """Milky Frog local coding agent."""


@app.command()
def doctor() -> None:
    """Check local configuration without making a model request."""
    settings = Settings.from_environment()
    checks = {
        "state directory": str(settings.home),
        "API key": "configured" if settings.api_key else "missing",
        "base URL": settings.base_url or "provider default",
        "model": settings.model or "missing",
    }
    table = Table(title="Milky Frog doctor")
    table.add_column("Check")
    table.add_column("Value")
    for name, value in checks.items():
        table.add_row(name, value)
    console.print(table)
    if not settings.api_key or not settings.model:
        raise typer.Exit(code=2)


@app.command("init")
def initialize(
    workspace: Annotated[Path | None, typer.Argument()] = None,
) -> None:
    """Create declarative project configuration and Skill directories."""
    root = (workspace or Path.cwd()).resolve(strict=True) / ".milky-frog"
    root.mkdir(exist_ok=True)
    (root / "skills").mkdir(exist_ok=True)
    config = root / "config.toml"
    if config.exists():
        console.print(f"[yellow]Already initialized:[/] {root}")
        return
    config.write_text(
        "# Project-level Milky Frog configuration.\nmax_model_calls = 30\n",
        encoding="utf-8",
    )
    console.print(f"[green]Initialized:[/] {root}")


@app.command("runs")
def list_runs() -> None:
    """List recent durable Runs."""
    store = SqliteCheckpointStore(Settings.from_environment().database_path)
    table = Table("Run", "Status", "Workspace", "Updated")
    for run in store.list_runs():
        table.add_row(run.run_id, run.status, str(run.workspace), run.updated_at.isoformat())
    console.print(table)


@app.command()
def show(
    run_id: Annotated[str, typer.Argument()],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show a Run and its Checkpoint events."""
    store = SqliteCheckpointStore(Settings.from_environment().database_path)
    run = store.get_run(run_id)
    if run is None:
        console.print(f"[red]Unknown Run:[/] {run_id}")
        raise typer.Exit(code=1)
    events = store.events(run_id)
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "run_id": run.run_id,
                    "status": run.status,
                    "workspace": str(run.workspace),
                    "events": [
                        {
                            "sequence": event.sequence,
                            "type": event.event_type,
                            "version": event.version,
                            "payload": event.payload,
                        }
                        for event in events
                    ],
                },
                ensure_ascii=False,
            )
        )
        return
    console.print(f"[bold]{run.run_id}[/]  {run.status}  {run.workspace}")
    for event in events:
        console.print(f"{event.sequence:>4}  {event.event_type}")


@app.command()
def run(task: Annotated[str, typer.Argument()]) -> None:
    """Start a Run (provider wiring is the next implementation slice)."""
    del task
    console.print("[red]Model adapter and built-in Tools are not wired yet.[/]")
    raise typer.Exit(code=2)


@app.command()
def resume(run_id: Annotated[str, typer.Argument()]) -> None:
    """Resume a Run (replay is the next implementation slice)."""
    del run_id
    console.print("[red]Checkpoint replay is not wired yet.[/]")
    raise typer.Exit(code=2)
