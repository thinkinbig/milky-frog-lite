"""In-process collector for the files a Run reads and edits.

Subscribes to the read-only ``EventHub`` (``RunAfterTool``) and records,
per ``run_id``, every ``read_file`` / ``edit_file`` path the agent touches —
the raw signal for read-noise scoring. It changes nothing about execution,
so it honours the notify-only handler contract (ADR-0012) and never touches
``runner.py``.

This is the eval's real-time "scale": measurement happens in-process the moment
a Run finishes, with no Langfuse round-trip. Langfuse remains the long-term
archive; this is the instrument.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from milky_frog.core.handlers import HandlerDeps
from milky_frog.events import EventHub, Handler, RunAfterTool


@dataclass(frozen=True, slots=True)
class ReadRecord:
    path: str
    is_error: bool


@dataclass(frozen=True, slots=True)
class FileTouch:
    """One file-touching Tool call, in observed order."""

    kind: str  # read | edit
    path: str
    is_error: bool


class ReadCollector(Handler):
    """Records read_file / edit_file paths per ``run_id`` for later scoring.

    ``RunAfterTool`` is one ordered stream, so the collector keeps it as one:
    ``touches`` is the ordered sequence, and ``reads`` / ``edits`` are per-kind
    projections of it. Recording only the projections would discard whether a
    read followed an edit of the same path — the distinction the edit -> re-read
    pathology turns on (#108 cause 1).
    """

    READ_TOOL = "read_file"
    EDIT_TOOLS = ("edit_file", "write_file")

    def __init__(self) -> None:
        self.touches: dict[str, list[FileTouch]] = defaultdict(list)

    @property
    def reads(self) -> dict[str, list[ReadRecord]]:
        return {
            run_id: [ReadRecord(t.path, t.is_error) for t in touches if t.kind == "read"]
            for run_id, touches in self.touches.items()
        }

    @property
    def edits(self) -> dict[str, list[str]]:
        return {
            run_id: [t.path for t in touches if t.kind == "edit"]
            for run_id, touches in self.touches.items()
        }

    def register(self, hub: EventHub) -> None:
        hub.on(RunAfterTool)(self._record)

    async def _record(self, event: RunAfterTool, deps: HandlerDeps | None = None) -> None:
        path = event.call.arguments.get("path")
        if not isinstance(path, str):
            return
        if event.call.name == self.READ_TOOL:
            kind = "read"
        elif event.call.name in self.EDIT_TOOLS:
            kind = "edit"
        else:
            return
        self.touches[event.run_id].append(FileTouch(kind, path, event.result.is_error))
