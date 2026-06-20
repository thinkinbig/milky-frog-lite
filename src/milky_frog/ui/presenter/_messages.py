from __future__ import annotations

from pathlib import Path

from rich.text import Text

from milky_frog.ui.presenter._base import _Surface


class _MessagesSurface(_Surface):
    def initialized(self, root: Path, *, already_exists: bool = False) -> None:
        if already_exists:
            message = Text("Already initialized: ", style="yellow")
        else:
            message = Text("Initialized: ", style="green")
        message.append(str(root))
        self.out.print(message)

    def error(self, message: str, *, hint: str | None = None) -> None:
        error = Text("Error: ", style="bold red")
        error.append(message)
        self.err.print(error)
        if hint:
            help_text = Text("Hint: ", style="bold cyan")
            help_text.append(hint)
            self.err.print(help_text)
