from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from milky_frog import __version__
from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.project import CONFIG_FILENAME, CONFIG_TEMPLATE, PROJECT_DIRNAME
from milky_frog.runtime import MilkyFrog, MissingModelConfiguration
from milky_frog.settings import Settings
from milky_frog.ui import (
    CheckStatus,
    Diagnostic,
    render_assistant,
    render_diagnostics,
    render_error,
    render_initialized,
    render_run,
    render_runs,
    run_interactive,
)

app = typer.Typer(
    no_args_is_help=False,
    rich_markup_mode="rich",
    help="[bold yellow]MILKY FROG[/] · 奶蛙\n\nA lightweight local coding agent.",
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit


@app.callback(invoke_without_command=True)
def main(
    context: typer.Context,
    version: Annotated[
        bool | None,
        typer.Option("--version", callback=_version_callback, is_eager=True),
    ] = None,
) -> None:
    """Milky Frog local coding agent."""
    if context.invoked_subcommand is None:
        interactive()


def interactive() -> None:
    """Run the foreground interactive task loop."""
    settings = Settings.from_environment()
    try:
        frog = MilkyFrog.from_settings(settings)
    except MissingModelConfiguration:
        _render_configuration_error()
        raise typer.Exit(code=2) from None
    workspace = Path.cwd()
    run_interactive(
        lambda task: frog.run(task, workspace),
        model=settings.model or "unknown",
        workspace=workspace,
    )


def _render_configuration_error() -> None:
    render_error(
        "Required model configuration is missing.",
        hint="Set MILKY_FROG_API_KEY and MILKY_FROG_MODEL, then run doctor.",
    )


@app.command()
def doctor() -> None:
    """Check local configuration without making a model request."""
    settings = Settings.from_environment()
    diagnostics = (
        Diagnostic("State directory", CheckStatus.PASS, str(settings.home)),
        Diagnostic(
            "API key",
            CheckStatus.PASS if settings.api_key else CheckStatus.FAIL,
            "configured" if settings.api_key else "missing (MILKY_FROG_API_KEY)",
        ),
        Diagnostic(
            "Base URL",
            CheckStatus.PASS if settings.base_url else CheckStatus.WARN,
            settings.base_url or "provider default",
        ),
        Diagnostic(
            "Model",
            CheckStatus.PASS if settings.model else CheckStatus.FAIL,
            settings.model or "missing (MILKY_FROG_MODEL)",
        ),
    )
    render_diagnostics(diagnostics)
    if not settings.api_key or not settings.model:
        render_error(
            "Required model configuration is missing.",
            hint="Set MILKY_FROG_API_KEY and MILKY_FROG_MODEL, then run doctor again.",
        )
        raise typer.Exit(code=2)


@app.command("init")
def initialize(
    workspace: Annotated[Path | None, typer.Argument()] = None,
) -> None:
    """Create declarative project configuration and Skill directories."""
    root = (workspace or Path.cwd()).resolve(strict=True) / PROJECT_DIRNAME
    root.mkdir(exist_ok=True)
    (root / "skills").mkdir(exist_ok=True)
    config = root / CONFIG_FILENAME
    if config.exists():
        render_initialized(root, already_exists=True)
        return
    config.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    render_initialized(root)


@app.command("runs")
def list_runs() -> None:
    """List recent durable Runs."""
    store = SqliteCheckpointStore(Settings.from_environment().database_path)
    render_runs(store.list_runs())


@app.command()
def show(
    run_id: Annotated[str, typer.Argument()],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show a Run and its Checkpoint events."""
    store = SqliteCheckpointStore(Settings.from_environment().database_path)
    run = store.get_run(run_id)
    if run is None:
        render_error(f"Unknown Run: {run_id}", hint="List available Runs with: milky-frog runs")
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
    render_run(run, events)


@app.command()
def run(task: Annotated[str, typer.Argument()]) -> None:
    """Start one foreground Run."""
    settings = Settings.from_environment()
    try:
        result = MilkyFrog.from_settings(settings).run(task, Path.cwd())
    except MissingModelConfiguration:
        _render_configuration_error()
        raise typer.Exit(code=2) from None
    except Exception as error:
        render_error(f"{type(error).__name__}: {error}")
        raise typer.Exit(code=1) from error
    render_assistant(result.final_message, run_id=result.run_id)


@app.command()
def resume(run_id: Annotated[str, typer.Argument()]) -> None:
    """Resume a Run (replay is the next implementation slice)."""
    del run_id
    render_error(
        "Checkpoint replay is not wired yet.",
        hint="Inspect the Run with: milky-frog show RUN_ID",
    )
    raise typer.Exit(code=2)
