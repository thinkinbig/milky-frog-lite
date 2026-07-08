from __future__ import annotations

from pathlib import Path

import typer

from milky_frog.app.session import AgentSession, MissingModelConfiguration
from milky_frog.project import SandboxConfigError, validate_sandbox_config
from milky_frog.settings import Settings
from milky_frog.tui.app import TuiLaunch
from milky_frog.tui.cli import render_error
from milky_frog.tui.runtime import run_tui


def interactive(*, launch: TuiLaunch | None = None) -> None:
    """Run the foreground interactive loop in full-screen TUI mode."""
    settings = Settings.from_environment()
    require_model_configuration_or_exit(settings)
    require_valid_sandbox_config_or_exit(Path.cwd())
    run_tui(settings, launch=launch)


def require_model_configuration_or_exit(settings: Settings) -> None:
    try:
        AgentSession.require_model_configuration(settings)
    except MissingModelConfiguration:
        render_configuration_error()
        raise typer.Exit(code=2) from None


def require_valid_sandbox_config_or_exit(workspace: Path) -> None:
    """Stop before a Run starts if [sandbox] is broken.

    Falling back to LocalSandbox here would silently run unsandboxed for a
    user who asked for container isolation — so this exits instead.
    """
    try:
        validate_sandbox_config(workspace)
    except SandboxConfigError as error:
        render_error(str(error), hint="Fix [sandbox] in .milky-frog/config.toml, then run doctor.")
        raise typer.Exit(code=2) from None


def render_configuration_error(*, run_doctor_again: bool = False) -> None:
    suffix = " again" if run_doctor_again else ""
    render_error(
        "Required model configuration is missing.",
        hint=f"Set MILKY_FROG_API_KEY and MILKY_FROG_MODEL, then run doctor{suffix}.",
    )
