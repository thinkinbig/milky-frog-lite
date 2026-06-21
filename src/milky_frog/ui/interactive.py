from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from rich.text import Text

from milky_frog.domain import RunStatus
from milky_frog.harness import ResumeError
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
from milky_frog.ui.protocols import RunAdvancer, RunCanceller
from milky_frog.ui.streaming import StreamingPrinter


def parse_resume_command(task: str) -> tuple[str, str | None] | None:
    """Return ``(run_id, prompt)`` for ``/resume RUN_ID [prompt]``, else ``None``."""
    if not task.casefold().startswith("/resume"):
        return None
    rest = task[len("/resume") :].strip()
    if not rest:
        return ("", None)
    run_id, _, prompt = rest.partition(" ")
    return run_id, prompt.strip() or None


def run_interactive(
    advance: RunAdvancer,
    *,
    model: str,
    workspace: Path,
    printer: StreamingPrinter,
    cancel: RunCanceller | None = None,
    resolve_run: Callable[[str], str] | None = None,
    recover_run: Callable[[], str | None] | None = None,
) -> None:
    """Own one foreground Terminal UI interaction loop.

    ``advance(task, run_id)`` starts a fresh Run when ``run_id`` is ``None`` and
    otherwise continues that Run with ``task`` as the next user turn, so the
    conversation accumulates one transcript across prompts. ``/clear`` drops the
    cursor to begin a new conversation.
    """
    render_interactive_welcome(model=model, workspace=workspace)
    run_id: str | None = None
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
            run_id = None
            continue

        resume_command = parse_resume_command(task)
        if resume_command is not None:
            attach_id, resume_prompt = resume_command
            if not attach_id:
                render_error(
                    "Usage: /resume RUN_ID [prompt]",
                    hint="List available Runs with: milky-frog runs",
                )
                continue
            if resolve_run is not None:
                try:
                    attach_id = resolve_run(attach_id)
                except ResumeError as error:
                    render_error(str(error), hint="List available Runs with: milky-frog runs")
                    continue
            if resume_prompt is None:
                run_id = attach_id
                console.print(
                    Text(
                        f"Attached to run {attach_id[:8]} · next prompt continues it",
                        style="dim",
                    )
                )
                continue
            run_id = attach_id
            task = resume_prompt

        render_interactive_statusbar(model=model, workspace=workspace, state="working")
        try:
            result = advance(task, run_id)
        except KeyboardInterrupt:
            if cancel is not None:
                cancel()
            printer.finish()
            if run_id is None and recover_run is not None:
                run_id = recover_run()
            render_error(
                "Cancelled the current task.",
                hint="Press Ctrl+C again at the prompt to exit.",
            )
            continue
        except Exception as error:
            printer.finish()
            render_error(f"{type(error).__name__}: {error}")
            continue
        # Continue this Run on the next prompt — including after a cancel or a
        # pause — so the conversation keeps one growing transcript.
        run_id = result.run_id
        if result.status is RunStatus.CANCELLED:
            printer.finish()
            render_error(
                "Cancelled the current task.",
                hint="Press Ctrl+C again at the prompt to exit.",
            )
            continue
        if printer.finish():
            render_assistant_footer(result.run_id, usage=result.usage)
        else:
            render_assistant(result.final_message, run_id=result.run_id, usage=result.usage)
        console.print()
