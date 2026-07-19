"""Checks that the collector preserves the order it observes.

``RunAfterTool`` arrives as one ordered stream. Splitting it into per-kind
lists — which is all the collector used to keep — throws away whether a read
came before or after an edit of the same path. That ordering is exactly the
question for the edit -> re-read pathology (#108 cause 1), so it is recorded.

    uv run pytest evals/test_read_collector.py -o addopts=""
"""

from __future__ import annotations

from pathlib import Path

from evals.read_collector import ReadCollector
from milky_frog.domain import RunState, ToolCall, ToolResult
from milky_frog.events import RunAfterTool

RUN = "run-1"


def _event(tool: str, path: str, *, is_error: bool = False) -> RunAfterTool:
    return RunAfterTool(
        run_id=RUN,
        call=ToolCall(id="c", name=tool, arguments={"path": path}),
        result=ToolResult("", is_error=is_error),
        state=RunState(run_id=RUN, workspace=Path(".")),
    )


async def _feed(collector: ReadCollector, events: list[RunAfterTool]) -> None:
    for event in events:
        await collector._record(event)


async def test_touches_preserve_interleaved_order() -> None:
    collector = ReadCollector()
    await _feed(
        collector,
        [
            _event("read_file", "a.py"),
            _event("edit_file", "a.py"),
            _event("read_file", "a.py"),
        ],
    )

    assert [(t.kind, t.path) for t in collector.touches[RUN]] == [
        ("read", "a.py"),
        ("edit", "a.py"),
        ("read", "a.py"),
    ]


async def test_read_and_edit_projections_still_work() -> None:
    """The flat per-kind views stay available for the order-free metrics."""
    collector = ReadCollector()
    await _feed(
        collector,
        [
            _event("read_file", "a.py"),
            _event("edit_file", "b.py"),
            _event("read_file", "c.py", is_error=True),
            _event("write_file", "d.py"),
        ],
    )

    assert [(r.path, r.is_error) for r in collector.reads[RUN]] == [
        ("a.py", False),
        ("c.py", True),
    ]
    assert collector.edits[RUN] == ["b.py", "d.py"]


async def test_non_file_tool_calls_are_ignored() -> None:
    collector = ReadCollector()
    await _feed(collector, [_event("bash", "a.py"), _event("read_file", "a.py")])

    assert [t.kind for t in collector.touches[RUN]] == ["read"]
