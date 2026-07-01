"""In-process collector for every Tool call in a Run.

Subscribes to ``RunAfterTool`` and records tool name, arguments, and whether
the result was an error — the raw signal for truncation-eval review.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from milky_frog.core.handlers import HandlerDeps
from milky_frog.events import EventHub, ObserverHandler, RunAfterTool


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    name: str
    arguments: dict[str, Any]
    is_error: bool


def summarize_tool_call(record: ToolCallRecord) -> str:
    """One-line summary for terminal output."""
    if record.name == "bash":
        command = record.arguments.get("command", "")
        if isinstance(command, str):
            text = command.strip().replace("\n", " ")
            if len(text) > 100:
                text = text[:97] + "..."
            return f"bash: {text}"
    if record.name == "read_file":
        path = record.arguments.get("path", "")
        return f"read_file: {path}"
    if record.name == "list_dir":
        path = record.arguments.get("path", ".")
        return f"list_dir: {path}"
    return f"{record.name}: {record.arguments}"


class ToolCallCollector(ObserverHandler):
    """Records every tool call per ``run_id`` for later scoring or review."""

    def __init__(self) -> None:
        self.calls: dict[str, list[ToolCallRecord]] = defaultdict(list)

    def register(self, hub: EventHub) -> None:
        hub.on(RunAfterTool)(self._record)

    async def _record(self, event: RunAfterTool, deps: HandlerDeps | None = None) -> None:
        self.calls[event.run_id].append(
            ToolCallRecord(
                name=event.call.name,
                arguments=dict(event.call.arguments),
                is_error=event.result.is_error,
            )
        )
