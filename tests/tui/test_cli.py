from __future__ import annotations

import io

import pytest
from rich.console import Console

from milky_frog.diagnostics import CheckStatus, Diagnostic
from milky_frog.tui import cli as tui_cli


@pytest.fixture
def captured_output(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    """Swap the module-level Rich console for one writing to an in-memory buffer.

    A wide width keeps the table from wrapping the diagnostic value across
    lines, which would otherwise split "[sandbox]" apart in the captured text.
    """
    buffer = io.StringIO()
    test_console = Console(file=buffer, width=200)
    monkeypatch.setattr(tui_cli, "console", test_console)
    return buffer


def test_render_diagnostics_preserves_literal_brackets_in_value(
    captured_output: io.StringIO,
) -> None:
    """A Diagnostic.value containing "[sandbox]" must render literally.

    Rich tables parse plain strings as markup by default, so an unescaped
    "[sandbox]" is interpreted as a (nonexistent) style tag and silently
    dropped instead of being printed.
    """
    diagnostics = (
        Diagnostic(
            "Sandbox",
            CheckStatus.FAIL,
            "invalid [sandbox] in config.toml: image is required when sandbox.kind = 'docker'",
        ),
    )

    tui_cli.render_diagnostics(diagnostics)

    assert "[sandbox]" in captured_output.getvalue()


def test_render_diagnostics_preserves_literal_brackets_in_name(
    captured_output: io.StringIO,
) -> None:
    """Diagnostic.name is escaped too, for the same reason as value."""
    diagnostics = (Diagnostic("[weird] name", CheckStatus.PASS, "ok"),)

    tui_cli.render_diagnostics(diagnostics)

    assert "[weird] name" in captured_output.getvalue()
