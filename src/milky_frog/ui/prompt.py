from __future__ import annotations

import sys
from pathlib import Path

from prompt_toolkit.application import Application, get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.history import FileHistory, History, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, VSplit, Window
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame, TextArea

from milky_frog.ui.console import BOX_WIDTH as _BOX_WIDTH

_STYLE = Style.from_dict(
    {
        "frame.border": "#5f5f5f",
        "prompt": "ansicyan bold",
    }
)

# Up/Down recall earlier inputs the way Claude's prompt does. In-memory by default so it
# works without any setup (and in tests); configure_history() upgrades it to a file so recall
# survives across Runs instead of resetting every time the process restarts.
_history: History = InMemoryHistory()


def configure_history(path: Path) -> None:
    """Persist prompt history to ``path`` so Up/Down recall survives process restarts."""
    global _history
    path.parent.mkdir(parents=True, exist_ok=True)
    _history = FileHistory(str(path))


def prompt_in_box() -> str:
    """Read one line of input inside a bordered box that stays closed while typing.

    The box is drawn with prompt_toolkit instead of rich so the frame encloses the
    live input rather than only bracketing it before and after submission, and Up/Down
    walk the in-memory history. Falls back to a plain readline when stdin/stdout are not
    a terminal (pipes, tests). Raises EOFError on Ctrl-D and KeyboardInterrupt on Ctrl-C,
    matching the contract the interactive loop already handles.
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return input("> ")

    captured: list[str] = []

    def accept(buffer: Buffer) -> bool:
        # prompt_toolkit appends accepted, non-empty input to the bound history itself
        # (Buffer.validate_and_handle), so we only capture the text and leave.
        captured.append(buffer.text)
        get_app().exit()
        return False

    text_area = TextArea(
        multiline=False,
        wrap_lines=False,
        prompt=[("class:prompt", "> ")],
        accept_handler=accept,
        history=_history,
    )

    bindings = KeyBindings()

    @bindings.add("c-c")
    def _interrupt(event: object) -> None:
        get_app().exit(exception=KeyboardInterrupt)

    @bindings.add("c-d")
    def _eof(event: object) -> None:
        get_app().exit(exception=EOFError)

    # Match the old rich prompt box: fill the terminal width, capped at _BOX_WIDTH, and
    # left-aligned. preferred forces the frame to expand instead of shrinking to content;
    # the filler soaks up any extra width past the cap.
    root = VSplit(
        [
            Frame(text_area, width=Dimension(preferred=_BOX_WIDTH, max=_BOX_WIDTH)),
            Window(),
        ]
    )
    app: Application[None] = Application(
        layout=Layout(root),
        key_bindings=bindings,
        style=_STYLE,
        full_screen=False,
        mouse_support=False,
    )
    app.run()
    return captured[0] if captured else ""
