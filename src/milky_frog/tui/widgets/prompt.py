from __future__ import annotations

from textual import events
from textual.widgets import Input

from milky_frog.tui.rendering import complete_command


class PromptInput(Input):
    """The task prompt, with Tab command-completion and Up/Down history recall."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._history: list[str] = []
        self._index: int | None = None  # cursor into history; None == live draft
        self._draft: str = ""

    def remember(self, value: str) -> None:
        """Record a submitted prompt and reset the recall cursor."""
        if value and (not self._history or self._history[-1] != value):
            self._history.append(value)
        self._index = None
        self._draft = ""

    async def on_key(self, event: events.Key) -> None:
        # Never intercept text/IME input (e.g. CJK characters): let the base
        # Input insert them. We only handle the navigation keys below.
        if event.is_printable:
            return
        if event.key == "tab":
            completion = complete_command(self.value)
            if completion is not None and completion != self.value:
                event.prevent_default()
                event.stop()
                self.value = completion
                self.cursor_position = len(self.value)
            return
        if event.key == "up":
            event.prevent_default()
            event.stop()
            self._recall_previous()
        elif event.key == "down":
            event.prevent_default()
            event.stop()
            self._recall_next()

    def _recall_previous(self) -> None:
        if not self._history:
            return
        if self._index is None:
            self._draft = self.value
            self._index = len(self._history) - 1
        elif self._index > 0:
            self._index -= 1
        self.value = self._history[self._index]
        self.cursor_position = len(self.value)

    def _recall_next(self) -> None:
        if self._index is None:
            return
        if self._index < len(self._history) - 1:
            self._index += 1
            self.value = self._history[self._index]
        else:
            self._index = None
            self.value = self._draft
        self.cursor_position = len(self.value)
