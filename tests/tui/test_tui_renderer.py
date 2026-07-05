from __future__ import annotations

from pathlib import Path

from milky_frog.domain import (
    ModelRequest,
    ModelResponse,
    RunResult,
    RunState,
    RunStatus,
    TextDelta,
    TokenUsage,
)
from milky_frog.events import EventHub
from milky_frog.events.events import (
    RunAfterModel,
    RunCompaction,
    RunModelChunk,
    RunNotice,
    RunPaused,
)
from milky_frog.tui.messages import (
    AddText,
    ApprovalRequired,
    CompactionMsg,
    RunNoticeMsg,
    UpdateUsage,
)
from milky_frog.tui.presentation import TuiPresentationHandler

_WORKSPACE = Path("/tmp")


class _MessageSink:
    def __init__(self) -> None:
        self.messages: list[object] = []

    def __call__(self, message: object) -> None:
        self.messages.append(message)


async def test_presentation_maps_run_notice() -> None:
    sink = _MessageSink()
    bus = EventHub()
    TuiPresentationHandler(sink).register(bus)

    await bus.broadcast(RunNotice(run_id="run-1", message="retrying connection", level="warning"))

    assert len(sink.messages) == 1
    message = sink.messages[0]
    assert isinstance(message, RunNoticeMsg)
    assert message.message == "retrying connection"
    assert message.level == "warning"


async def test_presentation_maps_model_chunk_and_approval_pause() -> None:
    sink = _MessageSink()
    bus = EventHub()
    TuiPresentationHandler(sink).register(bus)

    await bus.broadcast(
        RunModelChunk(
            run_id="run-1",
            request=ModelRequest((), ()),
            chunk=TextDelta("hi"),
        )
    )
    await bus.broadcast(
        RunPaused(
            run_id="run-1",
            result=RunResult(
                "run-1",
                RunStatus.WAITING_FOR_APPROVAL,
                "approval needed for: bash",
                1,
            ),
            state=RunState("run-1", _WORKSPACE),
        )
    )

    assert isinstance(sink.messages[0], AddText)
    assert sink.messages[0].text == "hi"
    assert isinstance(sink.messages[1], ApprovalRequired)
    assert sink.messages[1].reason == "approval needed for: bash"


async def test_presentation_accumulates_usage_on_after_model() -> None:
    sink = _MessageSink()
    bus = EventHub()
    TuiPresentationHandler(sink).register(bus)
    request = ModelRequest((), ())

    await bus.broadcast(
        RunAfterModel(
            run_id="run-1",
            request=request,
            response=ModelResponse(
                content="ok",
                tool_calls=(),
                usage=TokenUsage(input_tokens=3, output_tokens=5),
                model="test",
            ),
            state=RunState("run-1", _WORKSPACE),
        )
    )

    assert len(sink.messages) == 1
    update = sink.messages[0]
    assert isinstance(update, UpdateUsage)
    assert update.usage.cumulative.input_tokens == 3
    assert update.usage.cumulative.output_tokens == 5


async def test_presentation_compaction_bills_usage_and_emits_line() -> None:
    # The summarization call never reaches after_model, so the compaction event
    # must fold its cost into the running total — otherwise tokens under-report.
    sink = _MessageSink()
    bus = EventHub()
    TuiPresentationHandler(sink).register(bus)

    await bus.broadcast(
        RunCompaction(
            run_id="run-1",
            messages_folded=6,
            usage=TokenUsage(input_tokens=40, output_tokens=8),
        )
    )

    update = next(m for m in sink.messages if isinstance(m, UpdateUsage))
    assert update.usage.cumulative.input_tokens == 40
    assert update.usage.cumulative.output_tokens == 8
    line = next(m for m in sink.messages if isinstance(m, CompactionMsg))
    assert line.messages_folded == 6


async def test_presentation_compaction_without_usage_still_emits_line() -> None:
    # A provider that omits usage must not spuriously emit an UpdateUsage.
    sink = _MessageSink()
    bus = EventHub()
    TuiPresentationHandler(sink).register(bus)

    await bus.broadcast(
        RunCompaction(run_id="run-1", messages_folded=3, usage=TokenUsage()),
    )

    assert not any(isinstance(m, UpdateUsage) for m in sink.messages)
    assert any(isinstance(m, CompactionMsg) for m in sink.messages)
