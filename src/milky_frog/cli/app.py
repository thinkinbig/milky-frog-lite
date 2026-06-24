from __future__ import annotations

from typing import Annotated

import typer

from milky_frog import __version__
from milky_frog.cli.commands import register_commands
from milky_frog.cli.launch import interactive

# ── Typer app ─────────────────────────────────────────────────────────


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


register_commands(app)
