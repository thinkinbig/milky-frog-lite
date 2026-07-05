from __future__ import annotations

from typing import ClassVar, override

from rich.text import Text
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from milky_frog.tui.messages import ApprovalOptionSelected


def _approval_body(reason: str) -> str:
    """Drop the machine header and keep the user-facing question."""
    _, sep, tail = reason.partition("\n\n")
    return tail.strip() if sep else reason.strip()


class ApprovalPrompt(Vertical):
    """Inline approval menu: arrow keys to highlight, Enter to confirm."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("1", "pick_first", "Allow once", show=False, priority=True),
        Binding("2", "pick_second", "Always this tool", show=False, priority=True),
        Binding("3", "pick_third", "Always all", show=False, priority=True),
        Binding("4", "pick_fourth", "Deny", show=False, priority=True),
        Binding("5", "pick_fifth", "Deny with reason", show=False, priority=True),
    ]

    def __init__(self, *, tool_name: str, reason: str) -> None:
        super().__init__(classes="approval-prompt")
        self._tool_name = tool_name
        self._reason = reason

    @override
    def compose(self):
        header = f"Run {self._tool_name}?" if self._tool_name else "Tool approval required"
        yield Static(Text.assemble(("  ⚠ ", "bold yellow"), (header, "bold")), id="approval-header")
        yield Static(Text(f"  {_approval_body(self._reason)}", style="dim"), id="approval-body")
        options: list[Option] = [
            Option("  1. Allow once", id="approve"),
            Option(
                (
                    f"  2. Always allow {self._tool_name}"
                    if self._tool_name
                    else "  2. Always allow this tool"
                ),
                id="allow_tool",
                disabled=not self._tool_name,
            ),
            Option("  3. Always allow all tools", id="allow_all"),
            Option("  4. Deny", id="deny"),
            Option("  5. Deny and tell the agent why…", id="deny_reason"),
        ]
        yield OptionList(*options, id="approval-options")

    def on_mount(self) -> None:
        self.query_one("#approval-options", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id
        if option_id is not None:
            self.post_message(ApprovalOptionSelected(option_id))

    def _pick(self, index: int) -> None:
        options = self.query_one("#approval-options", OptionList)
        if 0 <= index < options.option_count:
            options.highlighted = index
            options.action_select()

    def action_pick_first(self) -> None:
        self._pick(0)

    def action_pick_second(self) -> None:
        self._pick(1)

    def action_pick_third(self) -> None:
        self._pick(2)

    def action_pick_fourth(self) -> None:
        self._pick(3)

    def action_pick_fifth(self) -> None:
        self._pick(4)
