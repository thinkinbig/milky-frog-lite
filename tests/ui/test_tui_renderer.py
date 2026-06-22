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
from milky_frog.handlers import EventDispatcher
from milky_frog.handlers.events import (
    RunAfterModel,
    RunModelChunk,
    RunNotice,
    RunPaused,
)
from milky_frog.ui.tui.messages import (
    AddText,
    ApprovalRequired,
    RunNoticeMsg,
    UpdateUsage,
)
from milky_frog.ui.tui.presentation import TuiPresentationHandler

_WORKSPACE = Path("/tmp")


class _MessageSink:
    def __init__(self) -> None:
        self.messages: list[object] = []

    def __call__(self, message: object) -> None:
        self.messages.append(message)


async def test_presentation_maps_run_notice() -> None:
    sink = _MessageSink()
    bus = EventDispatcher()
    TuiPresentationHandler(sink).register(bus)

    await bus.notify(RunNotice(run_id="run-1", message="retrying connection", level="warning"))

    assert len(sink.messages) == 1
    message = sink.messages[0]
    assert isinstance(message, RunNoticeMsg)
    assert message.message == "retrying connection"
    assert message.level == "warning"


async def test_presentation_maps_model_chunk_and_approval_pause() -> None:
    sink = _MessageSink()
    bus = EventDispatcher()
    TuiPresentationHandler(sink).register(bus)

    await bus.notify(
        RunModelChunk(
            run_id="run-1",
            request=ModelRequest((), ()),
            chunk=TextDelta("hi"),
        )
    )
    await bus.notify(
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
    bus = EventDispatcher()
    TuiPresentationHandler(sink).register(bus)
    request = ModelRequest((), ())

    await bus.notify(
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
