from __future__ import annotations

from contextlib import nullcontext
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

from milky_frog.domain import RunResult, RunStatus

interactive = import_module("milky_frog.ui.interactive")


class FakeConsole:
    def status(self, *args: object, **kwargs: object) -> Any:
        del args, kwargs
        return nullcontext()

    def print(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def clear(self) -> None:
        pass


def test_interactive_keyboard_interrupt_requests_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancelled = False
    prompts = iter(["build it"])

    def prompt() -> str:
        try:
            return next(prompts)
        except StopIteration:
            raise EOFError from None

    def execute(_task: str, _run_id: str | None) -> RunResult:
        raise KeyboardInterrupt

    def cancel() -> None:
        nonlocal cancelled
        cancelled = True

    monkeypatch.setattr(interactive, "prompt_in_box", prompt)
    monkeypatch.setattr(interactive, "console", FakeConsole())
    monkeypatch.setattr(interactive, "render_interactive_welcome", lambda **kwargs: None)
    monkeypatch.setattr(interactive, "render_interactive_statusbar", lambda **kwargs: None)
    monkeypatch.setattr(interactive, "render_error", lambda *args, **kwargs: None)

    interactive.run_interactive(
        execute,
        model="test-model",
        workspace=Path("/workspace"),
        printer=interactive.StreamingPrinter(),
        cancel=cancel,
    )

    assert cancelled is True


def test_interactive_cooperative_cancel_shows_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts = iter(["build it"])
    errors: list[str] = []

    def prompt() -> str:
        try:
            return next(prompts)
        except StopIteration:
            raise EOFError from None

    def execute(_task: str, _run_id: str | None) -> RunResult:
        return RunResult("run-1", RunStatus.CANCELLED, "cancelled", 0)

    monkeypatch.setattr(interactive, "prompt_in_box", prompt)
    monkeypatch.setattr(interactive, "console", FakeConsole())
    monkeypatch.setattr(interactive, "render_interactive_welcome", lambda **kwargs: None)
    monkeypatch.setattr(interactive, "render_interactive_statusbar", lambda **kwargs: None)
    monkeypatch.setattr(
        interactive,
        "render_error",
        lambda message, **kwargs: errors.append(message),
    )

    interactive.run_interactive(
        execute,
        model="test-model",
        workspace=Path("/workspace"),
        printer=interactive.StreamingPrinter(),
    )

    assert errors == ["Cancelled the current task."]


def test_interactive_terminal_owns_commands_and_run_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = iter(("/help", "build it", "/exit"))
    events: list[str] = []

    monkeypatch.setattr(interactive, "prompt_in_box", lambda: next(inputs))
    monkeypatch.setattr(interactive, "console", FakeConsole())
    monkeypatch.setattr(
        interactive,
        "render_interactive_welcome",
        lambda **kwargs: events.append(f"welcome:{kwargs['model']}"),
    )
    monkeypatch.setattr(interactive, "render_interactive_help", lambda: events.append("help"))
    monkeypatch.setattr(
        interactive,
        "render_assistant",
        lambda message, **kwargs: events.append(f"answer:{message}:{kwargs['run_id']}"),
    )

    def execute(task: str, run_id: str | None) -> RunResult:
        events.append(f"run:{task}")
        return RunResult("run-123", RunStatus.COMPLETED, "done", 1)

    interactive.run_interactive(
        execute,
        model="test-model",
        workspace=Path("/workspace"),
        printer=interactive.StreamingPrinter(),
    )

    assert events == [
        "welcome:test-model",
        "help",
        "run:build it",
        "answer:done:run-123",
    ]


def test_interactive_threads_run_id_across_turns_and_clear_resets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = iter(("first", "second", "/clear", "third", "/exit"))
    seen: list[str | None] = []

    monkeypatch.setattr(interactive, "prompt_in_box", lambda: next(inputs))
    monkeypatch.setattr(interactive, "console", FakeConsole())
    monkeypatch.setattr(interactive, "render_interactive_welcome", lambda **kwargs: None)
    monkeypatch.setattr(interactive, "render_interactive_statusbar", lambda **kwargs: None)
    monkeypatch.setattr(interactive, "render_assistant", lambda *args, **kwargs: None)

    def execute(task: str, run_id: str | None) -> RunResult:
        # Record the cursor each turn, then return a stable Run id for it.
        seen.append(run_id)
        return RunResult("conv-1", RunStatus.COMPLETED, "done", 1)

    interactive.run_interactive(
        execute,
        model="test-model",
        workspace=Path("/workspace"),
        printer=interactive.StreamingPrinter(),
    )

    # Turn 1 starts fresh (None); turn 2 continues the same Run; /clear drops the
    # cursor so turn 3 starts fresh again.
    assert seen == [None, "conv-1", None]
