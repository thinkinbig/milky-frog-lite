from __future__ import annotations

from pathlib import Path

from rich import box
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from milky_frog.ui.console import BOX_WIDTH
from milky_frog.ui.logo import pixel_frog_logo
from milky_frog.ui.presenter._base import _Surface


def _short_workspace(workspace: Path) -> str:
    resolved = workspace.expanduser().resolve()
    try:
        relative = resolved.relative_to(Path.home())
    except ValueError:
        return resolved.as_posix()
    return f"~/{relative.as_posix()}" if relative.parts else "~"


class _SessionSurface(_Surface):
    def welcome(self, *, model: str, workspace: Path) -> None:
        details = Table.grid(padding=(0, 1))
        details.add_column()
        title = Text("✻ ", style="bold yellow")
        title.append("Welcome to MILKY FROG", style="bold yellow")
        title.append(" · 奶蛙", style="bold white")
        details.add_row(title)
        details.add_row(Text("Local coding agent", style="dim"))
        details.add_row("")

        meta = Table.grid(padding=(0, 2))
        meta.add_column(style="dim", no_wrap=True)
        meta.add_column(overflow="fold")
        meta.add_row("model", model)
        meta.add_row("workspace", _short_workspace(workspace))
        details.add_row(meta)

        welcome = Table.grid(padding=(0, 3))
        welcome.add_column(no_wrap=True)
        welcome.add_column(overflow="fold")
        welcome.add_row(pixel_frog_logo(), details)

        self.out.print(
            Panel(
                welcome,
                border_style="yellow",
                box=box.ROUNDED,
                padding=(1, 2),
                width=BOX_WIDTH,
            )
        )

        tips = Table.grid(padding=(0, 1))
        tips.add_column()
        tips.add_row(Text("Tips for getting started", style="bold"))
        tips.add_row(Text("• Describe what to build, fix, or explain — be specific", style="dim"))
        tips.add_row(
            Text("• /help lists commands · /clear resets the screen · /exit leaves", style="dim")
        )
        self.out.print(tips)
        self.out.print()

    def statusbar(self, *, model: str, workspace: Path, state: str = "ready") -> None:
        status = Text("  ")
        status.append(model, style="dim")
        status.append("  ·  ", style="bright_black")
        status.append(_short_workspace(workspace), style="dim")
        status.append("  ·  ", style="bright_black")
        status.append(state, style="green" if state == "ready" else "yellow")
        self.out.print(status)

    def help(self) -> None:
        commands = Table.grid(padding=(0, 2))
        commands.add_column(style="yellow", no_wrap=True)
        commands.add_column(style="dim")
        commands.add_row("/help", "Show available commands")
        commands.add_row("/clear", "Clear the terminal")
        commands.add_row("/exit", "Leave Milky Frog")
        commands.add_row("exit · quit", "Leave Milky Frog")
        self.out.print(
            Panel(
                commands,
                title="Commands",
                border_style="bright_black",
                box=box.ROUNDED,
                expand=False,
            )
        )

    def assistant(self, message: str, *, run_id: str | None = None) -> None:
        body = Markdown(message) if message else Text("No response content.", style="dim")
        response = Table.grid(padding=(0, 1))
        response.add_column(no_wrap=True, vertical="top")
        response.add_column(ratio=1)
        response.add_row(Text("●", style="bold yellow"), body)
        self.out.print(response)
        if run_id:
            self.assistant_footer(run_id)

    def assistant_footer(self, run_id: str) -> None:
        self.out.print(Text(f"  ⎿ run {run_id[:8]}", style="bright_black"))
