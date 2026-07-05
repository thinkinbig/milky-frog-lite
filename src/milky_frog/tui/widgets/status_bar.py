from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.reactive import reactive
from textual.timer import Timer
from textual.widgets import Static

from milky_frog.domain import RunUsage
from milky_frog.tui.usage import context_fraction, format_context_meter, format_run_usage


class RunStatusBar(Static):
    """Bottom status line showing model, workspace, token usage, and run status."""

    status_text: reactive[str] = reactive("ready")

    def __init__(self, model: str, workspace: Path, context_window: int) -> None:
        super().__init__()
        self._model = model
        self._workspace = _short_workspace(workspace)
        self._context_window = context_window
        self._usage: RunUsage = RunUsage()
        self._run_id: str | None = None
        self._spinner_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self._frame_idx = 0
        self._timer: Timer | None = None

    def watch_status_text(self, value: str) -> None:
        if value in {"working", "cancelling"}:
            if self._timer is None:
                self._timer = self.set_interval(0.1, self._tick)
        else:
            if self._timer is not None:
                self._timer.stop()
                self._timer = None
        self.update(self._format(value))

    def _tick(self) -> None:
        self._frame_idx = (self._frame_idx + 1) % len(self._spinner_frames)
        self.update(self._format(self.status_text))

    def set_usage(self, usage: RunUsage) -> None:
        self._usage = usage
        self.refresh()

    def set_run_id(self, run_id: str) -> None:
        self._run_id = run_id
        self.refresh()

    def set_working(self) -> None:
        self.status_text = "working"

    def set_ready(self) -> None:
        self.status_text = "ready"

    def set_cancelling(self) -> None:
        self.status_text = "cancelling"

    def _format(self, state: str) -> Text:
        parts: list[Text] = []
        parts.append(Text(f" {self._model}", style="dim"))
        parts.append(Text("  ·  ", style="bright_black"))
        parts.append(Text(self._workspace, style="dim"))
        if self._run_id:
            parts.append(Text("  ·  ", style="bright_black"))
            parts.append(Text(f"run {self._run_id[:8]}", style="dim"))
        meter = format_context_meter(self._usage.context_tokens, self._context_window)
        if meter:
            parts.append(Text("  ·  ", style="bright_black"))
            parts.append(Text(meter, style=_context_meter_style(self._usage, self._context_window)))
        usage_str = format_run_usage(self._usage)
        if usage_str:
            parts.append(Text("  ·  ", style="bright_black"))
            parts.append(Text(usage_str, style="dim"))
        parts.append(Text("  ·  ", style="bright_black"))
        if state in {"working", "cancelling"}:
            spinner = self._spinner_frames[self._frame_idx]
            style = "yellow" if state == "working" else "bright_yellow"
            parts.append(Text(f"{spinner} {state}", style=style))
        else:
            parts.append(Text(state, style="green"))
        return Text.assemble(*parts)


def _context_meter_style(usage: RunUsage, context_window: int) -> str:
    """Dim until the context window fills up, then warn (yellow) and alarm (red)."""
    fraction = context_fraction(usage.context_tokens, context_window)
    if fraction is None:
        return "dim"
    if fraction >= 0.9:
        return "bold red"
    if fraction >= 0.75:
        return "yellow"
    return "dim"


def _short_workspace(workspace: Path) -> str:
    resolved = workspace.expanduser().resolve()
    try:
        relative = resolved.relative_to(Path.home())
    except ValueError:
        return resolved.as_posix()
    return f"~/{relative.as_posix()}" if relative.parts else "~"
