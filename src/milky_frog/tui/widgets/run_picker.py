from __future__ import annotations

from typing import ClassVar, override

from rich.text import Text
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from milky_frog.checkpoint import StoredRun
from milky_frog.tui.messages import RunOptionSelected


class RunPicker(Vertical):
    """Single-Run picker: arrows move, Enter resumes, Escape cancels."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "confirm", "Resume", show=True, priority=True),
        Binding("escape", "dismiss", "Cancel", show=False, priority=True),
    ]

    def __init__(self, runs: tuple[StoredRun, ...]) -> None:
        super().__init__(classes="run-picker")
        self._runs = runs

    @staticmethod
    def _option_label(run: StoredRun) -> Text:
        summary = (run.final_message or "No final message.").replace("\n", " ")
        summary = summary[:120] + ("..." if len(summary) > 120 else "")
        updated = run.updated_at.astimezone().strftime("%Y-%m-%d %H:%M")
        return Text.assemble(
            (run.run_id[:8], "bold"),
            ("  ", ""),
            (run.status.value, "cyan"),
            ("  ", ""),
            (updated, "dim"),
            ("  ", ""),
            (summary, "dim"),
        )

    @override
    def compose(self):
        yield Static(
            Text.assemble(
                ("  Select a Run  ", "bold"),
                ("  Up/Down to choose · Enter to resume · Esc to cancel", "dim"),
            ),
            id="run-picker-header",
        )
        options = [Option(self._option_label(run), id=run.run_id) for run in self._runs]
        yield OptionList(*options, id="run-options")

    def on_mount(self) -> None:
        self.query_one("#run-options", OptionList).focus()

    def action_confirm(self) -> None:
        options = self.query_one("#run-options", OptionList)
        highlighted = options.highlighted
        if highlighted is not None:
            self.post_message(RunOptionSelected(self._runs[highlighted].run_id))

    def action_dismiss(self) -> None:
        self.post_message(RunOptionSelected(None))
