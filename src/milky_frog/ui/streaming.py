from __future__ import annotations

from rich.console import Console
from rich.text import Text

from milky_frog.ui.console import console as _default_console
from milky_frog.ui.console import get_box_width


class StreamingPrinter:
    """Renders model reasoning and text deltas live to the console during a Run.

    One instance spans an interactive session. A reasoning model streams its
    thinking first (rendered dimmed under a marker), then the answer; ``finish``
    closes whichever block is open and returns whether anything was streamed, so
    callers can fall back to a one-shot render when the model produced no visible
    output (e.g. a stubbed runtime).
    """

    def __init__(self, out: Console | None = None) -> None:
        self._out = out or _default_console
        self._phase: str | None = None  # None | "reasoning" | "answer"
        self._block_console: Console | None = None  # width-locked for the current block

    def on_reasoning(self, text: str) -> None:
        if self._phase != "reasoning":
            self._out.print(Text("✻ thinking", style="dim italic"))
            self._phase = "reasoning"
            # Lock the width at the start of the block so every chunk in this
            # thinking stream is wrapped at the same column as the other boxes.
            self._block_console = Console(width=get_box_width())
        assert self._block_console is not None
        self._block_console.print(Text(text, style="dim"), end="")

    def on_delta(self, text: str) -> None:
        if self._phase == "reasoning":
            assert self._block_console is not None
            self._block_console.print()  # close the thinking block before the answer
            self._block_console = None
        if self._phase != "answer":
            self._out.print(Text("● ", style="bold yellow"), end="")
            self._phase = "answer"
        self._out.print(text, end="", markup=False, highlight=False)

    def finish(self) -> bool:
        streamed = self._phase is not None
        if streamed:
            self._out.print()
        self._phase = None
        return streamed
