from __future__ import annotations

from contextlib import nullcontext
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

from milky_frog.domain import ResumeError, RunResult, RunStatus
from tests.stubs import (
    NoOpArgsKwargs,
    NoOpKwargs,
    RecordingAssistant,
    RecordingError,
    RecordingHelp,
    RecordingWelcome,
    ScriptedPrompt,
)

interactive = import_module("milky_frog.ui.interactive")


class FakeConsole:
    def status(self, *args: object, **kwargs: object) -> Any:
        del args, kwargs
        return nullcontext()

    def print(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def clear(self) -> None:
        pass


class KeyboardInterruptAdvancer:
    def __call__(self, _task: str, _run_id: str | None) -> RunResult:
        raise KeyboardInterrupt


class CancelledAdvancer:
    def __call__(self, _task: str, _run_id: str | None) -> RunResult:
        return RunResult("run-1", RunStatus.CANCELLED, "cancelled", 0)


class RecordingAdvancer:
    def __init__(self, events: list[str], *, run_id: str = "run-123") -> None:
        self._events = events
        self._run_id = run_id

    def __call__(self, task: str, run_id: str | None) -> RunResult:
        self._events.append(f"run:{task}")
        del run_id
        return RunResult(self._run_id, RunStatus.COMPLETED, "done", 1)


class ThreadingAdvancer:
    def __init__(self, seen: list[str | None]) -> None:
        self._seen = seen

    def __call__(self, task: str, run_id: str | None) -> RunResult:
        del task
        self._seen.append(run_id)
        return RunResult("conv-1", RunStatus.COMPLETED, "done", 1)


class FlagCanceller:
    def __init__(self) -> None:
        self.cancelled = False

    def __call__(self) -> None:
        self.cancelled = True


def test_interactive_keyboard_interrupt_requests_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canceller = FlagCanceller()

    monkeypatch.setattr(interactive, "prompt_in_box", ScriptedPrompt(("build it",)))
    monkeypatch.setattr(interactive, "console", FakeConsole())
    monkeypatch.setattr(interactive, "render_interactive_welcome", NoOpKwargs())
    monkeypatch.setattr(interactive, "render_interactive_statusbar", NoOpKwargs())
    monkeypatch.setattr(interactive, "render_error", NoOpArgsKwargs())

    interactive.run_interactive(
        KeyboardInterruptAdvancer(),
        model="test-model",
        workspace=Path("/workspace"),
        printer=interactive.StreamingPrinter(),
        cancel=canceller,
    )

    assert canceller.cancelled is True


def test_interactive_cooperative_cancel_shows_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errors: list[str] = []

    monkeypatch.setattr(interactive, "prompt_in_box", ScriptedPrompt(("build it",)))
    monkeypatch.setattr(interactive, "console", FakeConsole())
    monkeypatch.setattr(interactive, "render_interactive_welcome", NoOpKwargs())
    monkeypatch.setattr(interactive, "render_interactive_statusbar", NoOpKwargs())
    monkeypatch.setattr(interactive, "render_error", RecordingError(errors))

    interactive.run_interactive(
        CancelledAdvancer(),
        model="test-model",
        workspace=Path("/workspace"),
        printer=interactive.StreamingPrinter(),
    )

    assert errors == ["Cancelled the current task."]


def test_interactive_terminal_owns_commands_and_run_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    monkeypatch.setattr(
        interactive,
        "prompt_in_box",
        ScriptedPrompt(("/help", "build it", "/exit")),
    )
    monkeypatch.setattr(interactive, "console", FakeConsole())
    monkeypatch.setattr(interactive, "render_interactive_welcome", RecordingWelcome(events))
    monkeypatch.setattr(interactive, "render_interactive_help", RecordingHelp(events))
    monkeypatch.setattr(interactive, "render_assistant", RecordingAssistant(events))

    interactive.run_interactive(
        RecordingAdvancer(events),
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
    seen: list[str | None] = []

    monkeypatch.setattr(
        interactive,
        "prompt_in_box",
        ScriptedPrompt(("first", "second", "/clear", "third", "/exit")),
    )
    monkeypatch.setattr(interactive, "console", FakeConsole())
    monkeypatch.setattr(interactive, "render_interactive_welcome", NoOpKwargs())
    monkeypatch.setattr(interactive, "render_interactive_statusbar", NoOpKwargs())
    monkeypatch.setattr(interactive, "render_assistant", NoOpArgsKwargs())

    interactive.run_interactive(
        ThreadingAdvancer(seen),
        model="test-model",
        workspace=Path("/workspace"),
        printer=interactive.StreamingPrinter(),
    )

    # Turn 1 starts fresh (None); turn 2 continues the same Run; /clear drops the
    # cursor so turn 3 starts fresh again.
    assert seen == [None, "conv-1", None]


def test_parse_resume_command() -> None:
    assert interactive.parse_resume_command("hello") is None
    assert interactive.parse_resume_command("/resume run-123") == ("run-123", None)
    assert interactive.parse_resume_command("/resume run-123 follow up") == (
        "run-123",
        "follow up",
    )
    assert interactive.parse_resume_command("/RESUME run-123") == ("run-123", None)


def test_interactive_resume_attaches_run_and_threads_next_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str | None] = []
    printed: list[str] = []

    class PrintingConsole(FakeConsole):
        def print(self, *args: object, **kwargs: object) -> None:
            del kwargs
            if args and hasattr(args[0], "plain"):
                printed.append(str(args[0].plain))

    monkeypatch.setattr(
        interactive,
        "prompt_in_box",
        ScriptedPrompt(("/resume run-attach", "next turn", "/exit")),
    )
    monkeypatch.setattr(interactive, "console", PrintingConsole())
    monkeypatch.setattr(interactive, "render_interactive_welcome", NoOpKwargs())
    monkeypatch.setattr(interactive, "render_interactive_statusbar", NoOpKwargs())
    monkeypatch.setattr(interactive, "render_assistant", NoOpArgsKwargs())

    interactive.run_interactive(
        ThreadingAdvancer(seen),
        model="test-model",
        workspace=Path("/workspace"),
        printer=interactive.StreamingPrinter(),
        resolve_run=lambda run_id: run_id,
    )

    assert seen == ["run-attach"]
    assert any("Attached to run run-atta" in line for line in printed)


def test_interactive_resume_with_prompt_runs_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    monkeypatch.setattr(
        interactive,
        "prompt_in_box",
        ScriptedPrompt(("/resume run-attach keep going", "/exit")),
    )
    monkeypatch.setattr(interactive, "console", FakeConsole())
    monkeypatch.setattr(interactive, "render_interactive_welcome", NoOpKwargs())
    monkeypatch.setattr(interactive, "render_interactive_statusbar", NoOpKwargs())
    monkeypatch.setattr(interactive, "render_assistant", RecordingAssistant(events))

    interactive.run_interactive(
        RecordingAdvancer(events, run_id="run-attach"),
        model="test-model",
        workspace=Path("/workspace"),
        printer=interactive.StreamingPrinter(),
        resolve_run=lambda run_id: run_id,
    )

    assert events == ["run:keep going", "answer:done:run-attach"]


def test_interactive_resume_rejects_unknown_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errors: list[str] = []

    monkeypatch.setattr(
        interactive,
        "prompt_in_box",
        ScriptedPrompt(("/resume missing-run", "/exit")),
    )
    monkeypatch.setattr(interactive, "console", FakeConsole())
    monkeypatch.setattr(interactive, "render_interactive_welcome", NoOpKwargs())
    monkeypatch.setattr(interactive, "render_error", RecordingError(errors))

    def reject_unknown(run_id: str) -> str:
        if run_id == "missing-run":
            raise ResumeError(f"unknown Run: {run_id}")
        return run_id

    interactive.run_interactive(
        ThreadingAdvancer([]),
        model="test-model",
        workspace=Path("/workspace"),
        printer=interactive.StreamingPrinter(),
        resolve_run=reject_unknown,
    )

    assert errors == ["unknown Run: missing-run"]


def test_interactive_keyboard_interrupt_recovers_latest_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str | None] = []

    monkeypatch.setattr(
        interactive,
        "prompt_in_box",
        ScriptedPrompt(("first task", "second task", "/exit")),
    )
    monkeypatch.setattr(interactive, "console", FakeConsole())
    monkeypatch.setattr(interactive, "render_interactive_welcome", NoOpKwargs())
    monkeypatch.setattr(interactive, "render_interactive_statusbar", NoOpKwargs())
    monkeypatch.setattr(interactive, "render_assistant", NoOpArgsKwargs())
    monkeypatch.setattr(interactive, "render_error", NoOpArgsKwargs())

    class InterruptThenContinueAdvancer:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, task: str, run_id: str | None) -> RunResult:
            self.calls += 1
            if self.calls == 1:
                raise KeyboardInterrupt
            seen.append(run_id)
            return RunResult("conv-1", RunStatus.COMPLETED, "done", 1)

    interactive.run_interactive(
        InterruptThenContinueAdvancer(),
        model="test-model",
        workspace=Path("/workspace"),
        printer=interactive.StreamingPrinter(),
        recover_run=lambda: "conv-1",
    )

    assert seen == ["conv-1"]
