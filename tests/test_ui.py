from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

from rich.console import Console

from milky_frog.checkpoint import RunEvent, StoredRun
from milky_frog.domain import RunStatus
from milky_frog.ui import presenter


def _capture_consoles(monkeypatch: object) -> tuple[StringIO, StringIO]:
    stdout = StringIO()
    stderr = StringIO()
    monkeypatch.setattr(presenter, "console", Console(file=stdout, color_system=None, width=120))
    monkeypatch.setattr(
        presenter,
        "error_console",
        Console(file=stderr, color_system=None, width=120),
    )
    return stdout, stderr


def test_render_runs_shows_actionable_empty_state(monkeypatch: object) -> None:
    stdout, _ = _capture_consoles(monkeypatch)

    presenter.render_runs(())

    assert stdout.getvalue() == "No runs yet.\nStart one with: milky-frog run TASK\n"


def test_render_interactive_welcome_shows_context(monkeypatch: object) -> None:
    stdout, _ = _capture_consoles(monkeypatch)

    presenter.render_interactive_welcome(
        model="deepseek-v4-flash", workspace=Path("/workspace/milky-frog")
    )

    rendered = stdout.getvalue()
    assert "MILKY FROG" in rendered
    assert "奶蛙" in rendered
    assert "deepseek-v4-flash" in rendered
    assert "/workspace/milky-frog" in rendered
    assert "████" in rendered


def test_render_interactive_help_lists_local_commands(monkeypatch: object) -> None:
    stdout, _ = _capture_consoles(monkeypatch)

    presenter.render_interactive_help()

    rendered = stdout.getvalue()
    assert "/help" in rendered
    assert "/clear" in rendered
    assert "/exit" in rendered


def test_render_run_shows_summary_and_events_without_payload(monkeypatch: object) -> None:
    stdout, _ = _capture_consoles(monkeypatch)
    now = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
    run = StoredRun("run-123", Path("/workspace"), RunStatus.COMPLETED, now, now)
    events = (RunEvent("RunStarted", {"prompt": "secret"}, sequence=1, created_at=now),)

    presenter.render_run(run, events)

    output = stdout.getvalue()
    assert "run-123" in output
    assert "completed" in output
    assert "RunStarted" in output
    assert "secret" not in output


def test_render_error_uses_stderr_and_escapes_markup(monkeypatch: object) -> None:
    stdout, stderr = _capture_consoles(monkeypatch)

    presenter.render_error("Unknown Run: [bold]not markup[/]", hint="Run milky-frog runs")

    assert stdout.getvalue() == ""
    assert "[bold]not markup[/]" in stderr.getvalue()
    assert "Hint: Run milky-frog runs" in stderr.getvalue()
