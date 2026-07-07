from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Input

from milky_frog.harness.mcp.config import load_mcp_config, set_server_enabled
from milky_frog.tui.messages import McpReloadRequested
from milky_frog.tui.viewmodels.protocols import TuiHost
from milky_frog.tui.widgets.mcp_picker import McpPicker


class McpViewModel:
    """Manages MCP server enable/disable selection from the TUI."""

    def __init__(self, app: TuiHost) -> None:
        self._app = app
        self._picker: McpPicker | None = None

    @property
    def has_picker(self) -> bool:
        return self._picker is not None

    def dismiss_picker(self) -> None:
        if self._picker is not None:
            self._picker.action_dismiss()

    def handle_command(self) -> None:
        """Open the MCP server picker."""
        home = self._app.session.home
        cfg = load_mcp_config(home, Path.cwd())

        if not cfg.mcpServers:
            self._append("No MCP servers configured.", style="dim")
            self._append(f"Add servers to {home}/mcp.json to get started.", style="dim")
            return

        servers: tuple[tuple[str, str, bool], ...] = tuple(
            (name, f"{srv.command} {' '.join(srv.args)}".strip(), srv.enabled)
            for name, srv in cfg.mcpServers.items()
        )
        self._show_picker(servers)

    def _show_picker(self, servers: tuple[tuple[str, str, bool], ...]) -> None:
        if self._picker is not None:
            self._picker.remove()
        picker = McpPicker(servers)
        self._picker = picker
        conversation = self._app.query_one("#conversation", VerticalScroll)
        conversation.mount(picker)
        conversation.scroll_end(animate=False)
        self._app.query_one("#prompt-input", Input).disabled = True

    def on_picker_confirmed(self, enabled: frozenset[str]) -> None:
        if self._picker is not None:
            self._picker.remove()
            self._picker = None
        self._app.query_one("#prompt-input", Input).disabled = False
        self._app.query_one("#prompt-input", Input).focus()

        home = self._app.session.home
        workspace = Path.cwd()
        cfg = load_mcp_config(home, workspace)
        changed = False

        for name, srv in cfg.mcpServers.items():
            want_enabled = name in enabled
            if srv.enabled != want_enabled:
                try:
                    set_server_enabled(home, name, enabled=want_enabled, workspace=workspace)
                    changed = True
                except Exception as exc:
                    self._append(f"Failed to update {name!r}: {exc}", style="bold red")

        if changed:
            self._append("Reconnecting MCP servers…", style="dim")
            self._app.post_message(McpReloadRequested())
        else:
            self._append("No changes.", style="dim")

    def _append(self, text: str, *, style: str = "dim") -> None:
        self._app._append(Text(text, style=style))
