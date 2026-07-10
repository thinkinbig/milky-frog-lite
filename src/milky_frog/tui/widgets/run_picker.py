from __future__ import annotations

from typing import ClassVar, override

from rich.text import Text
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from milky_frog.checkpoint import StoredRun
from milky_frog.tui.messages import RunOptionSelected


def _summary(run: StoredRun) -> str:
    message = (run.final_message or "No final message").replace("\n", " ").strip()
    return message[:80] + ("…" if len(message) > 80 else "")


class RunPicker(Vertical):
    """Single-select picker for recent Runs in the current Workspace."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss", "Cancel", show=False, priority=True),
    ]

    def __init__(self, runs: tuple[StoredRun, ...]) -> None:
        super().__init__(classes="run-picker")
        self._runs = runs

    @override
    def compose(self):
        yield Static(
            Text.assemble(
                ("  Resume a Run  ", "bold"),
                ("  ↑/↓ to choose · Enter to resume · Esc to cancel", "dim"),
            ),
            id="run-picker-header",
        )
        options = [
            Option(
                Text.assemble(
                    (run.run_id[:8], "bold cyan"),
                    (f"  {run.status.value}", "yellow"),
                    (f"  {run.updated_at.astimezone().strftime('%Y-%m-%d %H:%M')}", "dim"),
                    (f"  — {_summary(run)}", "dim"),
                ),
                id=run.run_id,
            )
            for run in self._runs
        ]
        yield OptionList(*options, id="run-list")

    def on_mount(self) -> None:
        self.query_one("#run-list", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        run_id = event.option.id
        if run_id is not None:
            self.post_message(RunOptionSelected(run_id))

    def action_dismiss(self) -> None:
        self.post_message(RunOptionSelected(None))
