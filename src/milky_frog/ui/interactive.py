from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from milky_frog.domain import RunResult
from milky_frog.ui.console import console
from milky_frog.ui.presenter import (
    render_assistant,
    render_assistant_footer,
    render_error,
    render_interactive_help,
    render_interactive_statusbar,
    render_interactive_welcome,
)
from milky_frog.ui.prompt import prompt_in_box
from milky_frog.ui.streaming import StreamingPrinter


def run_interactive(
    execute: Callable[[str], RunResult],
    *,
    model: str,
    workspace: Path,
    printer: StreamingPrinter,
) -> None:
    """Own one foreground Terminal UI interaction loop."""
    render_interactive_welcome(model=model, workspace=workspace)
    while True:
        try:
            task = prompt_in_box().strip()
        except (EOFError, KeyboardInterrupt):
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

        render_interactive_statusbar(model=model, workspace=workspace, state="working")
        try:
            result = execute(task)
        except KeyboardInterrupt:
            printer.finish()
            render_error(
                "Cancelled the current task.",
                hint="Press Ctrl+C again at the prompt to exit.",
            )
            continue
        except Exception as error:
            printer.finish()
            render_error(f"{type(error).__name__}: {error}")
            continue
        if printer.finish():
            render_assistant_footer(result.run_id)
        else:
            render_assistant(result.final_message, run_id=result.run_id)
        console.print()
