from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.cli.actions import (
    build_doctor_diagnostics,
    initialize_workspace,
    load_run_view,
    prune_runs,
)
from milky_frog.cli.launch import (
    interactive,
    render_configuration_error,
    require_model_configuration_or_exit,
)
from milky_frog.cli.runs import find_last_run, resolve_run_id
from milky_frog.domain import ResumeError
from milky_frog.harness.mcp.config import MCP_CONFIG_FILENAME, load_mcp_config, set_server_enabled
from milky_frog.settings import Settings
from milky_frog.tui.app import TuiLaunch
from milky_frog.tui.cli import (
    console,
    render_diagnostics,
    render_error,
    render_initialized,
    render_run,
    render_runs,
)


def register_commands(app: typer.Typer) -> None:
    app.command()(doctor)
    app.command("init")(initialize)
    app.command("runs")(list_runs)
    app.command()(show)
    app.command()(run)
    app.command()(resume)
    app.command()(prune)

    mcp_app = typer.Typer(help="Manage MCP server configuration.")
    app.add_typer(mcp_app, name="mcp")
    mcp_app.command("list")(mcp_list)
    mcp_app.command("enable")(mcp_enable)
    mcp_app.command("disable")(mcp_disable)


def doctor() -> None:
    """Check local configuration without making a model request."""
    settings = Settings.from_environment()
    diagnostics = build_doctor_diagnostics(settings)
    render_diagnostics(diagnostics)
    if not settings.api_key or not settings.model:
        render_configuration_error(run_doctor_again=True)
        raise typer.Exit(code=2)


def initialize(
    workspace: Annotated[Path | None, typer.Argument()] = None,
) -> None:
    """Create declarative project configuration and Skill directories."""
    try:
        result = initialize_workspace(workspace)
    except OSError as error:
        render_error(
            f"Could not initialize workspace: {error}",
            hint="Choose a writable directory path.",
        )
        raise typer.Exit(code=1) from error
    render_initialized(result.root, already_exists=result.already_exists)


def list_runs() -> None:
    """List recent durable Runs."""
    store = SqliteCheckpointStore(Settings.from_environment().database_path)
    render_runs(store.list_runs())


def show(
    run_id: Annotated[str, typer.Argument()],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show a Run and its Checkpoint snapshot."""
    try:
        view = load_run_view(Settings.from_environment(), run_id)
    except LookupError:
        render_error(f"unknown Run: {run_id}", hint="List available Runs with: milky-frog runs")
        raise typer.Exit(code=1) from None
    if as_json:
        typer.echo(view.to_json())
        return
    render_run(view.run, view.state)


def run(task: Annotated[str, typer.Argument()]) -> None:
    """Start a foreground Run in the interactive TUI."""
    interactive(launch=TuiLaunch(prompt=task))


def resume(
    run_id: Annotated[str | None, typer.Argument()] = None,
    task: Annotated[str | None, typer.Argument()] = None,
) -> None:
    """Resume a Run in the interactive TUI.

    Without RUN_ID, resumes the most recent Run in the current workspace.
    Without TASK, advances pending work (paused, cancelled, or orphaned). With
    TASK, appends a new user turn and advances — including terminal Runs.
    """
    settings = Settings.from_environment()
    require_model_configuration_or_exit(settings)
    if run_id is None:
        store = SqliteCheckpointStore(settings.database_path)
        run_id = find_last_run(store, Path.cwd())
        if run_id is None:
            render_error(
                "No recent Runs found in this workspace.",
                hint='Start a new Run with: milky-frog run "your task"',
            )
            raise typer.Exit(code=1)
    try:
        resolved = resolve_run_id(settings, run_id)
    except ResumeError as error:
        render_error(str(error), hint="List available Runs with: milky-frog runs")
        raise typer.Exit(code=1) from error
    interactive(
        launch=TuiLaunch(
            run_id=resolved,
            prompt=task,
            advance_pending=task is None,
        )
    )


def prune(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would be pruned without deleting"),
    ] = False,
    days: Annotated[
        int | None,
        typer.Option("--days", help="Override retention period (default: from config)"),
    ] = None,
) -> None:
    """Remove stale checkpoint snapshots older than the retention period.

    Scoped to the current Workspace. Never prunes RUNNING or
    WAITING_FOR_INPUT Runs.
    """
    try:
        result = prune_runs(Settings.from_environment(), Path.cwd(), dry_run=dry_run, days=days)
    except ValueError:
        render_error("retention period must be at least 1 day")
        raise typer.Exit(code=1) from None
    console.print()
    if result.dry_run:
        console.print(
            f"  [yellow]{result.count}[/] run(s) would be pruned "
            f"(retention: {result.retention_days} days)"
        )
    else:
        console.print(
            f"  Pruned [green]{result.count}[/] run(s) (retention: {result.retention_days} days)"
        )


def mcp_list() -> None:
    """List configured MCP servers and their enabled/disabled status."""
    settings = Settings.from_environment()
    config_path = settings.home / MCP_CONFIG_FILENAME
    if not config_path.exists():
        console.print(f"  No MCP config found at [dim]{config_path}[/]")
        console.print("  Create it to add MCP servers.")
        return

    cfg = load_mcp_config(settings.home)
    if not cfg.mcpServers:
        console.print("  No MCP servers configured.")
        return

    console.print()
    for name, srv in cfg.mcpServers.items():
        status = "[green]enabled[/]" if srv.enabled else "[yellow]disabled[/]"
        args_str = " ".join(srv.args) if srv.args else ""
        console.print(f"  {status}  [bold]{name}[/]  [dim]{srv.command} {args_str}[/]")
    console.print()


def mcp_enable(name: Annotated[str, typer.Argument(help="Server name to enable.")]) -> None:
    """Enable an MCP server."""
    settings = Settings.from_environment()
    try:
        set_server_enabled(settings.home, name, enabled=True)
    except KeyError:
        render_error(f"MCP server {name!r} not found in config.")
        raise typer.Exit(code=1) from None
    except OSError as exc:
        render_error(str(exc))
        raise typer.Exit(code=1) from exc
    console.print(f"  MCP server [bold]{name}[/] [green]enabled[/]. Restart milky-frog to apply.")


def mcp_disable(name: Annotated[str, typer.Argument(help="Server name to disable.")]) -> None:
    """Disable an MCP server without removing it from config."""
    settings = Settings.from_environment()
    try:
        set_server_enabled(settings.home, name, enabled=False)
    except KeyError:
        render_error(f"MCP server {name!r} not found in config.")
        raise typer.Exit(code=1) from None
    except OSError as exc:
        render_error(str(exc))
        raise typer.Exit(code=1) from exc
    console.print(f"  MCP server [bold]{name}[/] [yellow]disabled[/]. Restart milky-frog to apply.")
