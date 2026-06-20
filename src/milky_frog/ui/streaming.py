from __future__ import annotations

from rich.console import Console
from rich.text import Text

from milky_frog.ui.console import console


class StreamingPrinter:
    """Renders model reasoning and text deltas live to the console during a Run.

    One instance spans an interactive session. A reasoning model streams its
    thinking first (rendered dimmed under a marker), then the answer; ``finish``
    closes whichever block is open and returns whether anything was streamed, so
    callers can fall back to a one-shot render when the model produced no visible
    output (e.g. a stubbed runtime).
    """

    def __init__(self, out: Console | None = None) -> None:
        self._out = out or console
        self._phase: str | None = None  # None | "reasoning" | "answer"

    def on_reasoning(self, text: str) -> None:
        if self._phase != "reasoning":
            self._out.print(Text("✻ thinking", style="dim italic"))
            self._phase = "reasoning"
        self._out.print(Text(text, style="dim"), end="")

    def on_delta(self, text: str) -> None:
        if self._phase == "reasoning":
            self._out.print()  # close the thinking block before the answer
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
