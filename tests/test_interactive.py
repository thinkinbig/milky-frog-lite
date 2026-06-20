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

    def execute(task: str) -> RunResult:
        events.append(f"run:{task}")
        return RunResult("run-123", RunStatus.COMPLETED, "done", 1)

    interactive.run_interactive(
        execute,
        model="test-model",
        workspace=Path("/workspace"),
    )

    assert events == [
        "welcome:test-model",
        "help",
        "run:build it",
        "answer:done:run-123",
    ]
