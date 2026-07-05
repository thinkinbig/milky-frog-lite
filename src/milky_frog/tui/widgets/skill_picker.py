from __future__ import annotations

from typing import ClassVar, override

from rich.text import Text
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.widgets import SelectionList, Static
from textual.widgets.selection_list import Selection

from milky_frog.harness.skills import SkillSummary
from milky_frog.tui.messages import SkillOptionSelected


class SkillPicker(Vertical):
    """Multi-select skill picker: Space toggles, Enter confirms, Esc cancels."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "confirm", "Confirm", show=True, priority=True),
        Binding("escape", "dismiss", "Cancel", show=False, priority=True),
    ]

    def __init__(
        self,
        entries: tuple[tuple[SkillSummary, int], ...],
        active: frozenset[str],
    ) -> None:
        super().__init__(classes="skill-picker")
        self._entries = entries
        self._active = active

    @override
    def compose(self):
        yield Static(
            Text.assemble(
                ("  Select skills  ", "bold"),
                ("  Space to toggle · Enter to confirm · Esc to cancel", "dim"),
            ),
            id="skill-picker-header",
        )
        selections: list[Selection[str]] = [
            Selection(
                f"[bold]{s.name}[/bold]  [dim]— {s.description}[/dim]  [cyan]~{tok} tok[/cyan]",
                s.name,
                initial_state=s.name in self._active,
            )
            for s, tok in self._entries
        ]
        yield SelectionList(*selections, id="skill-list")

    def on_mount(self) -> None:
        self.query_one("#skill-list", SelectionList).focus()

    def action_confirm(self) -> None:
        widget = self.query_one("#skill-list", SelectionList)
        selected: frozenset[str] = frozenset(widget.selected)
        self.post_message(SkillOptionSelected(selected))

    def action_dismiss(self) -> None:
        self.post_message(SkillOptionSelected(self._active))  # no change
