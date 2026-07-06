from __future__ import annotations

from typing import ClassVar, override

from rich.text import Text
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.widgets import SelectionList, Static
from textual.widgets.selection_list import Selection

from milky_frog.tui.messages import McpOptionSelected


class McpPicker(Vertical):
    """Multi-select MCP server picker: Space toggles, Enter confirms, Esc cancels."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "confirm", "Confirm", show=True, priority=True),
        Binding("escape", "dismiss", "Cancel", show=False, priority=True),
    ]

    def __init__(
        self,
        servers: tuple[tuple[str, str, bool], ...],
    ) -> None:
        """
        Args:
            servers: ``(name, command_display, currently_enabled)`` tuples.
        """
        super().__init__(classes="mcp-picker")
        self._servers = servers
        self._initial: frozenset[str] = frozenset(name for name, _, enabled in servers if enabled)

    @override
    def compose(self):  # type: ignore[override]
        yield Static(
            Text.assemble(
                ("  MCP servers  ", "bold"),
                ("  Space to toggle · Enter to confirm · Esc to cancel", "dim"),
            ),
            id="mcp-picker-header",
        )
        selections: list[Selection[str]] = [
            Selection(
                Text.assemble(
                    (name, "bold"),
                    ("  ", ""),
                    (cmd, "dim"),
                ),
                name,
                initial_state=enabled,
            )
            for name, cmd, enabled in self._servers
        ]
        yield SelectionList(*selections, id="mcp-list")

    def on_mount(self) -> None:
        self.query_one("#mcp-list", SelectionList).focus()

    def action_confirm(self) -> None:
        widget = self.query_one("#mcp-list", SelectionList)
        enabled: frozenset[str] = frozenset(widget.selected)
        self.post_message(McpOptionSelected(enabled))

    def action_dismiss(self) -> None:
        self.post_message(McpOptionSelected(self._initial))  # no change
