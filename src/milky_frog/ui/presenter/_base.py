from __future__ import annotations

from rich.console import Console


class _Surface:
    """Base for the Terminal UI surfaces: holds the two output streams as state.

    Each surface (diagnostics, runs, session, messages) lives in its own module so it
    reads locally; composing them into one ``Presenter`` keeps the stdout/stderr split
    (ADR-0006) decided in a single place instead of in every render function.
    """

    out: Console
    err: Console
