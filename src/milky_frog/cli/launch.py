from __future__ import annotations

import typer

from milky_frog.app.session import AgentSession, MissingModelConfiguration
from milky_frog.settings import Settings
from milky_frog.ui.app import TuiLaunch
from milky_frog.ui.cli import render_error
from milky_frog.ui.runtime import run_tui


def interactive(*, launch: TuiLaunch | None = None) -> None:
    """Run the foreground interactive loop in full-screen TUI mode."""
    settings = Settings.from_environment()
    require_model_configuration_or_exit(settings)
    run_tui(settings, launch=launch)


def require_model_configuration_or_exit(settings: Settings) -> None:
    try:
        AgentSession.require_model_configuration(settings)
    except MissingModelConfiguration:
        render_configuration_error()
        raise typer.Exit(code=2) from None


def render_configuration_error(*, run_doctor_again: bool = False) -> None:
    suffix = " again" if run_doctor_again else ""
    render_error(
        "Required model configuration is missing.",
        hint=f"Set MILKY_FROG_API_KEY and MILKY_FROG_MODEL, then run doctor{suffix}.",
    )
