"""In-process collector for the files a Run reads and edits.

Subscribes to the read-only ``LifecycleBus`` (``RunAfterTool``) and records,
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

from milky_frog.handlers import BaseHandler, LifecycleBus, RunAfterTool


@dataclass(frozen=True, slots=True)
class ReadRecord:
    path: str
    is_error: bool


class ReadCollector(BaseHandler):
    """Records read_file / edit_file paths per ``run_id`` for later scoring."""

    READ_TOOL = "read_file"
    EDIT_TOOLS = ("edit_file", "write_file")

    def __init__(self) -> None:
        self.reads: dict[str, list[ReadRecord]] = defaultdict(list)
        self.edits: dict[str, list[str]] = defaultdict(list)

    def register(self, registry: LifecycleBus) -> None:
        registry.on(RunAfterTool)(self._record)

    async def _record(self, event: RunAfterTool) -> None:
        path = event.call.arguments.get("path")
        if not isinstance(path, str):
            return
        if event.call.name == self.READ_TOOL:
            self.reads[event.run_id].append(ReadRecord(path, event.result.is_error))
        elif event.call.name in self.EDIT_TOOLS:
            self.edits[event.run_id].append(path)
