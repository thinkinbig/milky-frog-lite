from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class HandlerResult(Protocol):
    """Marker for values returned by handlers that affect execution flow.

    A handler that returns ``None`` (the default) is pure observation.
    Returning a ``HandlerResult`` signals intent to control the Harness.
    Each event type defines which result types it accepts.
    """

    ...


@dataclass(frozen=True, slots=True)
class BlockResult:
    """Return from a ``BeforeTool`` handler to deny execution."""

    reason: str


@dataclass(frozen=True, slots=True)
class ApprovalResult:
    """Return from a handler to pause the Run for user approval."""

    reason: str = "needs approval"


class UIService(Protocol):
    """Abstract interface for UI operations available to all handlers.

    Concrete implementations wrap a UI framework (Textual, CLI, …).
    Handlers that receive an ``UIService`` via ``HandlerContext`` can
    interact with the frontend without depending on a specific toolkit.
    """

    def notify(self, message: str, level: str = "info") -> None:
        """Show a non-blocking notification to the user (info / warning / error)."""
        ...

    def set_status(self, key: str, text: str | None) -> None:
        """Set (or clear, when *text* is ``None``) a named status indicator."""
        ...

    def set_widget(self, key: str, lines: list[str] | None) -> None:
        """Set (or clear) a named widget display shown in the UI."""
        ...


class ConsoleUIService:
    """A no-op ``UIService`` that prints to stderr.

    Useful as a default when no TUI is active (CLI mode, tests).
    """

    def notify(self, message: str, level: str = "info") -> None:
        import sys

        tag = {"info": "", "warning": "⚠ ", "error": "✗ "}.get(level, "")
        print(f"{tag}{message}", file=sys.stderr)

    def set_status(self, key: str, text: str | None) -> None:
        pass  # no persistent status bar in CLI mode

    def set_widget(self, key: str, lines: list[str] | None) -> None:
        pass  # no widget area in CLI mode


@dataclass
class HandlerContext:
    """Shared framework-managed resources injected into every handler at notify time.

    Handlers receive this alongside the event so they can access
    framework services without wiring them through constructors.
    Fields that are ``None`` are unavailable in the current mode
    (for example ``ui`` in headless CLI mode).
    """

    ui: UIService | None = None
