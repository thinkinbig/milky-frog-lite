from __future__ import annotations

from rich.console import Console

from milky_frog.ui.console import console, error_console
from milky_frog.ui.presenter._diagnostics import _DiagnosticsSurface
from milky_frog.ui.presenter._messages import _MessagesSurface
from milky_frog.ui.presenter._runs import _RunsSurface
from milky_frog.ui.presenter._session import _SessionSurface


class Presenter(_DiagnosticsSurface, _RunsSurface, _SessionSurface, _MessagesSurface):
    """The Terminal UI seam: render Run state, results, and errors to two streams.

    Construct one over any pair of consoles — real ones in production, in-memory ones in
    tests — so the interface is the test surface. Each surface's rendering lives in its own
    module (``_diagnostics``, ``_runs``, ``_session``, ``_messages``); this class composes
    them and owns the stdout/stderr split (ADR-0006).
    """

    def __init__(self, out: Console, err: Console) -> None:
        self.out = out
        self.err = err

    @classmethod
    def default(cls) -> Presenter:
        """A Presenter over the shared stdout/stderr consoles."""
        return cls(console, error_console)


# The default adapter, plus back-compat free-function names for callers that just want to
# render to the shared streams. The Presenter is the real seam; these are bound methods of it.
_default = Presenter.default()

render_diagnostics = _default.diagnostics
render_runs = _default.runs
render_run = _default.run
render_interactive_welcome = _default.welcome
render_interactive_statusbar = _default.statusbar
render_interactive_help = _default.help
render_assistant = _default.assistant
render_assistant_footer = _default.assistant_footer
render_error = _default.error
render_initialized = _default.initialized

__all__ = [
    "Presenter",
    "render_assistant",
    "render_assistant_footer",
    "render_diagnostics",
    "render_error",
    "render_initialized",
    "render_interactive_help",
    "render_interactive_statusbar",
    "render_interactive_welcome",
    "render_run",
    "render_runs",
]
