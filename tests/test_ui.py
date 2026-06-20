from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

from rich.console import Console

from milky_frog.checkpoint import RunEvent, StoredRun
from milky_frog.diagnostics import CheckStatus, Diagnostic
from milky_frog.domain import RunStatus, RunUsage, TokenUsage
from milky_frog.ui.presenter import Presenter
from milky_frog.ui.streaming import StreamingPrinter


def _presenter() -> tuple[Presenter, StringIO, StringIO]:
    stdout = StringIO()
    stderr = StringIO()
    presenter = Presenter(
        out=Console(file=stdout, color_system=None, width=120),
        err=Console(file=stderr, color_system=None, width=120),
    )
    return presenter, stdout, stderr


def test_render_runs_shows_actionable_empty_state() -> None:
    presenter, stdout, _ = _presenter()

    presenter.runs(())

    assert stdout.getvalue() == "No runs yet.\nStart one with: milky-frog run TASK\n"


def test_streaming_printer_usage_closes_answer_and_resets_phase() -> None:
    out = StringIO()
    printer = StreamingPrinter(Console(file=out, color_system=None, width=120))

    printer.on_delta("partial answer")
    printer.usage("↑ 1.5k in · ↓ 340 out · Σ 1.9k tokens")

    output = out.getvalue()
    # The open answer line is closed before the dim usage line is printed.
    assert "partial answer\n" in output
    assert "⎿ ↑ 1.5k in · ↓ 340 out · Σ 1.9k tokens" in output
    # Phase reset: a following turn opens a fresh answer marker.
    assert printer.finish() is False


def test_assistant_footer_shows_token_summary_when_recorded() -> None:
    presenter, stdout, _ = _presenter()
    usage = RunUsage(cumulative=TokenUsage(input_tokens=1536, output_tokens=340))

    presenter.assistant_footer("run-1234abcd", usage=usage)

    output = stdout.getvalue()
    assert "run run-1234" in output
    assert "↑ 1.5k in · ↓ 340 out · Σ 1.9k tokens" in output


def test_assistant_footer_omits_summary_without_usage() -> None:
    presenter, stdout, _ = _presenter()

    presenter.assistant_footer("run-1234abcd", usage=RunUsage())

    assert stdout.getvalue().strip() == "⎿ run run-1234"


def test_render_diagnostics_summarizes_failures() -> None:
    presenter, stdout, _ = _presenter()

    presenter.diagnostics(
        (
            Diagnostic("API key", CheckStatus.FAIL, "missing"),
            Diagnostic("Base URL", CheckStatus.WARN, "default"),
        )
    )

    rendered = stdout.getvalue()
    assert "API key" in rendered
    assert "Doctor found 1 failure(s) and 1 warning(s)." in rendered


def test_render_interactive_welcome_shows_context() -> None:
    presenter, stdout, _ = _presenter()

    presenter.welcome(model="deepseek-v4-flash", workspace=Path("/workspace/milky-frog"))

    rendered = stdout.getvalue()
    assert "MILKY FROG" in rendered
    assert "奶蛙" in rendered
    assert "deepseek-v4-flash" in rendered
    assert "/workspace/milky-frog" in rendered
    assert "████" in rendered


def test_render_interactive_statusbar_shows_model_workspace_and_state() -> None:
    presenter, stdout, _ = _presenter()

    presenter.statusbar(
        model="deepseek-v4-flash",
        workspace=Path.home() / "CodeProject" / "milky-frog-lite",
        state="ready",
    )

    rendered = stdout.getvalue()
    assert "deepseek-v4-flash" in rendered
    assert "~/CodeProject/milky-frog-lite" in rendered
    assert "ready" in rendered


def test_render_interactive_help_lists_local_commands() -> None:
    presenter, stdout, _ = _presenter()

    presenter.help()

    rendered = stdout.getvalue()
    assert "/help" in rendered
    assert "/clear" in rendered
    assert "/exit" in rendered


def test_render_run_shows_summary_and_events_without_payload() -> None:
    presenter, stdout, _ = _presenter()
    now = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
    run = StoredRun("run-123", Path("/workspace"), RunStatus.COMPLETED, now, now)
    events = (RunEvent("RunStarted", {"prompt": "secret"}, sequence=1, created_at=now),)

    presenter.run(run, events)

    output = stdout.getvalue()
    assert "run-123" in output
    assert "completed" in output
    assert "RunStarted" in output
    assert "secret" not in output


def test_render_error_uses_stderr_and_escapes_markup() -> None:
    presenter, stdout, stderr = _presenter()

    presenter.error("Unknown Run: [bold]not markup[/]", hint="Run milky-frog runs")

    assert stdout.getvalue() == ""
    assert "[bold]not markup[/]" in stderr.getvalue()
    assert "Hint: Run milky-frog runs" in stderr.getvalue()
