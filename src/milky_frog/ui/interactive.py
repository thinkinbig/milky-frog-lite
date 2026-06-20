from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import typer

from milky_frog.domain import RunResult
from milky_frog.ui.console import console
from milky_frog.ui.presenter import (
    render_assistant,
    render_error,
    render_interactive_help,
    render_interactive_statusbar,
    render_interactive_welcome,
    render_prompt_box,
)

_PROMPT = (
    typer.style("│", fg=typer.colors.BRIGHT_BLACK)
    + " "
    + typer.style(">", fg=typer.colors.CYAN, bold=True)
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
        render_prompt_box(top=True)
        try:
            task = typer.prompt(_PROMPT, prompt_suffix=" ").strip()
        except (typer.Abort, EOFError, KeyboardInterrupt):
            render_prompt_box(top=False)
            typer.echo()
            return
        render_prompt_box(top=False)
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

        render_interactive_statusbar(model=model, workspace=workspace, state="working")
        try:
            with console.status(
                "[yellow]Milky Frog is thinking…[/]",
                spinner="dots",
                spinner_style="yellow",
            ):
                result = execute(task)
        except KeyboardInterrupt:
            typer.echo()
            render_error("Cancelled the current task.", hint="Press Ctrl+C again at the prompt to exit.")
            continue
        except Exception as error:
            render_error(f"{type(error).__name__}: {error}")
            continue
        render_assistant(result.final_message, run_id=result.run_id)
        console.print()
