from __future__ import annotations

from pathlib import Path

from milky_frog.domain import (
    ModelRequest,
    ModelResponse,
    RunRequest,
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
    RunCompleted,
    RunModelChunk,
    RunNotice,
    RunPaused,
    RunStarted,
)
from milky_frog.tui.messages import (
    AddText,
    ApprovalRequired,
    CompactionMsg,
    RunFinished,
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


async def test_presentation_ignores_nested_run_events() -> None:
    """A nested ``subagent`` Run shares the hub but must never be mistaken for
    the Run currently being presented — no spurious usage reset, no spurious
    RunFinished while the outer Run is still going."""
    sink = _MessageSink()
    bus = EventHub()
    TuiPresentationHandler(sink).register(bus)

    await bus.broadcast(
        RunStarted(
            run_id="outer",
            request=RunRequest("go", _WORKSPACE),
            state=RunState("outer", _WORKSPACE),
        )
    )
    await bus.broadcast(
        RunAfterModel(
            run_id="outer",
            request=ModelRequest((), ()),
            response=ModelResponse(usage=TokenUsage(input_tokens=10, output_tokens=10)),
            state=RunState("outer", _WORKSPACE),
        )
    )

    # A nested Run starts (and finishes) synchronously underneath one of the
    # outer Run's own tool calls, sharing the same hub.
    await bus.broadcast(
        RunStarted(
            run_id="nested",
            request=RunRequest("investigate", _WORKSPACE),
            state=RunState("nested", _WORKSPACE),
        )
    )
    await bus.broadcast(
        RunModelChunk(run_id="nested", request=ModelRequest((), ()), chunk=TextDelta("thinking"))
    )
    await bus.broadcast(
        RunCompleted(
            run_id="nested",
            result=RunResult("nested", RunStatus.COMPLETED, "nested report", 1),
            state=RunState("nested", _WORKSPACE),
        )
    )

    # The nested Run's chunk must not have been rendered, and its completion
    # must not have posted RunFinished for the still-running outer Run.
    assert not any(isinstance(m, AddText) and m.text == "thinking" for m in sink.messages)
    assert not any(isinstance(m, RunFinished) for m in sink.messages)

    # The outer Run's own usage survives the nested Run's RunStarted — it was
    # not reset back to zero.
    update = next(m for m in sink.messages if isinstance(m, UpdateUsage))
    assert update.usage.cumulative.input_tokens == 10

    # The outer Run finishing for real still reports normally.
    await bus.broadcast(
        RunCompleted(
            run_id="outer",
            result=RunResult("outer", RunStatus.COMPLETED, "outer done", 1),
            state=RunState("outer", _WORKSPACE),
        )
    )
    finished = next(m for m in sink.messages if isinstance(m, RunFinished))
    assert finished.result.run_id == "outer"
