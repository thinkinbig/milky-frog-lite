from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Input

from milky_frog.harness.skills import SkillCatalog, SkillSummary
from milky_frog.project import project_root
from milky_frog.tokens.base import ApproxCharCounter
from milky_frog.tui.viewmodels.protocols import TuiHost
from milky_frog.tui.widgets.skill_picker import SkillPicker


class SkillsViewModel:
    """Manages skill selection state and UI interactions.

    Owns the active-skill set, the picker widget lifecycle,
    and the /skill command handler logic.
    """

    def __init__(self, app: TuiHost) -> None:
        self._app = app
        self._active: frozenset[str] = frozenset()
        self._picker: SkillPicker | None = None
        self._touched: bool = False

    @property
    def active(self) -> frozenset[str]:
        return self._active

    @property
    def touched(self) -> bool:
        """True once the user changes skills in this session."""
        return self._touched

    @property
    def has_picker(self) -> bool:
        return self._picker is not None

    def dismiss_picker(self) -> None:
        if self._picker is not None:
            self._picker.action_dismiss()

    def handle_command(self, task: str) -> None:
        """Handle ``/skill [name|off]``."""
        parts = task.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""

        skills_home = self._app.session.skills_home
        catalog = SkillCatalog(skills_home, project_root(Path.cwd()) / "skills")

        if not arg:
            summaries = catalog.summaries()
            if not summaries:
                self._append("No skills found.")
                return
            self._show_picker(catalog, summaries)
            return

        if arg in ("off", "none", "clear"):
            self._active = frozenset()
            self._touched = True
            self._append("Skills deactivated.")
            self._update_placeholder()
            return

        summaries = catalog.summaries()
        names = {s.name for s in summaries}
        if arg not in names:
            self._append(f"Unknown skill: {arg!r}. Use /skill to list.", style="bold red")
            return

        if arg in self._active:
            self._active = self._active - {arg}
            self._append(f"Skill removed: {arg}")
        else:
            self._active = self._active | {arg}
            self._append(f"Skill added: {arg}", style="yellow")
        self._touched = True
        self._update_placeholder()

    def _show_picker(self, catalog: SkillCatalog, summaries: tuple[SkillSummary, ...]) -> None:
        counter = ApproxCharCounter()
        entries: list[tuple[SkillSummary, int]] = []
        for s in summaries:
            try:
                tok = counter.count_text(catalog.load(s.name).instructions)
            except Exception:
                tok = 0
            entries.append((s, tok))
        if self._picker is not None:
            self._picker.remove()
        picker = SkillPicker(tuple(entries), self._active)
        self._picker = picker
        conversation = self._app.query_one("#conversation", VerticalScroll)
        conversation.mount(picker)
        conversation.scroll_end(animate=False)
        self._app.query_one("#prompt-input", Input).disabled = True

    def on_picker_confirmed(self, selected: frozenset[str]) -> None:
        if self._picker is not None:
            self._picker.remove()
            self._picker = None
        self._app.query_one("#prompt-input", Input).disabled = False
        self._app.query_one("#prompt-input", Input).focus()
        if selected != self._active:
            self._active = selected
            self._touched = True
            if selected:
                names = ", ".join(sorted(selected))
                self._append(f"Active skills: {names}", style="yellow")
            else:
                self._append("Skills deactivated.")
        self._update_placeholder()

    def _update_placeholder(self) -> None:
        prompt_input = self._app.query_one("#prompt-input", Input)
        if self._active:
            names = ", ".join(sorted(self._active))
            prompt_input.placeholder = f"[skills: {names}] Type a task..."
        else:
            prompt_input.placeholder = "Type a task and press Enter..."

    def _append(self, text: str, *, style: str = "dim") -> None:
        self._app._append(Text(text, style=style))
