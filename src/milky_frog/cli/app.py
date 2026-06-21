from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from milky_frog import __version__
from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.cli.advance import MilkyFrogAdvancer
from milky_frog.cli.factory import HandlerFactory
from milky_frog.diagnostics import CheckStatus, Diagnostic
from milky_frog.harness import ResumeError
from milky_frog.project import CONFIG_FILENAME, CONFIG_TEMPLATE, PROJECT_DIRNAME
from milky_frog.runtime import MilkyFrog, MissingModelConfiguration
from milky_frog.settings import Settings
from milky_frog.ui import (
    StreamingPrinter,
    render_assistant,
    render_assistant_footer,
    render_diagnostics,
    render_error,
    render_initialized,
    render_run,
    render_runs,
    run_interactive,
)
from milky_frog.ui.prompt import configure_history


def _build_streaming_frog(settings: Settings) -> tuple[MilkyFrog, StreamingPrinter]:
    """Assemble a MilkyFrog whose model text streams live to the console."""
    # Fail fast before HandlerFactory builds resource-holding bundles (e.g. the
    # Langfuse client), so a missing configuration doesn't leak them.
    MilkyFrog.require_model_configuration(settings)
    printer = StreamingPrinter()
    registry, bundles = HandlerFactory(settings, printer).build()
    return MilkyFrog.from_settings(settings, registry, bundles), printer


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
        frog, printer = _build_streaming_frog(settings)
    except MissingModelConfiguration:
        _render_configuration_error()
        raise typer.Exit(code=2) from None
    workspace = Path.cwd()
    configure_history(settings.home / "prompt_history")
    with frog:
        run_interactive(
            MilkyFrogAdvancer(frog, workspace),
            model=settings.model or "unknown",
            workspace=workspace,
            printer=printer,
            cancel=frog.cancel,
        )


def _render_configuration_error(*, run_doctor_again: bool = False) -> None:
    suffix = " again" if run_doctor_again else ""
    render_error(
        "Required model configuration is missing.",
        hint=f"Set MILKY_FROG_API_KEY and MILKY_FROG_MODEL, then run doctor{suffix}.",
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
        _render_configuration_error(run_doctor_again=True)
        raise typer.Exit(code=2)


@app.command("init")
def initialize(
    workspace: Annotated[Path | None, typer.Argument()] = None,
) -> None:
    """Create declarative project configuration and Skill directories."""
    root = (workspace or Path.cwd()).expanduser().resolve() / PROJECT_DIRNAME
    try:
        root.mkdir(parents=True, exist_ok=True)
        (root / "skills").mkdir(exist_ok=True)
    except OSError as error:
        render_error(
            f"Could not initialize workspace: {error}",
            hint="Choose a writable directory path.",
        )
        raise typer.Exit(code=1) from error
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
        frog, printer = _build_streaming_frog(settings)
    except MissingModelConfiguration:
        _render_configuration_error()
        raise typer.Exit(code=2) from None
    with frog:
        try:
            result = frog.run(task, Path.cwd())
        except Exception as error:
            printer.finish()
            render_error(f"{type(error).__name__}: {error}")
            raise typer.Exit(code=1) from error
    if printer.finish():
        render_assistant_footer(result.run_id, usage=result.usage)
    else:
        render_assistant(result.final_message, run_id=result.run_id, usage=result.usage)


@app.command()
def resume(run_id: Annotated[str, typer.Argument()]) -> None:
    """Resume a stopped Run, advancing its pending work from the Checkpoint."""
    settings = Settings.from_environment()
    try:
        frog, printer = _build_streaming_frog(settings)
    except MissingModelConfiguration:
        _render_configuration_error()
        raise typer.Exit(code=2) from None
    with frog:
        try:
            result = frog.resume(run_id)
        except ResumeError as error:
            printer.finish()
            render_error(str(error), hint="List resumable Runs with: milky-frog runs")
            raise typer.Exit(code=1) from error
        except Exception as error:
            printer.finish()
            render_error(f"{type(error).__name__}: {error}")
            raise typer.Exit(code=1) from error
    if printer.finish():
        render_assistant_footer(result.run_id, usage=result.usage)
    else:
        render_assistant(result.final_message, run_id=result.run_id, usage=result.usage)
