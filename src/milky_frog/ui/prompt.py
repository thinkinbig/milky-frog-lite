from __future__ import annotations

import sys

from prompt_toolkit.application import Application, get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, VSplit, Window
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame, TextArea

_BOX_WIDTH = 92

_STYLE = Style.from_dict(
    {
        "frame.border": "#5f5f5f",
        "prompt": "ansicyan bold",
    }
)

# Shared across the session so Up/Down recall earlier inputs the way Claude's prompt does.
_HISTORY = InMemoryHistory()


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
        text = buffer.text
        captured.append(text)
        if text.strip():
            _HISTORY.append_string(text)
        get_app().exit()
        return False

    text_area = TextArea(
        multiline=False,
        wrap_lines=False,
        prompt=[("class:prompt", "> ")],
        accept_handler=accept,
        history=_HISTORY,
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
