from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import typer
from rich.rule import Rule

from milky_frog.domain import RunResult
from milky_frog.ui.console import console
from milky_frog.ui.presenter import (
    render_assistant,
    render_error,
    render_interactive_help,
    render_interactive_welcome,
)


def run_interactive(
    execute: Callable[[str], RunResult],
    *,
    model: str,
    workspace: Path,
) -> None:
    """Own one foreground Terminal UI interaction loop."""
    render_interactive_welcome(model=model, workspace=workspace)
    while True:
        console.print(Rule(style="bright_black"))
        try:
            task = typer.prompt(
                typer.style("You", fg=typer.colors.CYAN, bold=True),
                prompt_suffix="  > ",
            ).strip()
        except (typer.Abort, EOFError, KeyboardInterrupt):
            typer.echo()
            return
        if not task:
            continue

        command = task.casefold()
        if command in {"exit", "quit", "/exit"}:
            return
        if command in {"?", "/help"}:
            render_interactive_help()
            console.print()
            continue
        if command == "/clear":
            console.clear()
            continue

        try:
            with console.status(
                "[yellow]Milky Frog is thinking…[/]",
                spinner="dots",
                spinner_style="yellow",
            ):
                result = execute(task)
        except KeyboardInterrupt:
            typer.echo()
            return
        except Exception as error:
            render_error(f"{type(error).__name__}: {error}")
            continue
        render_assistant(result.final_message, run_id=result.run_id)
        console.print()
