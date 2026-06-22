from __future__ import annotations

from pathlib import Path

from textual.message import Message

from milky_frog.domain import (
    ModelRequest,
    ModelResponse,
    RunState,
    RunStatus,
    TextDelta,
    TokenUsage,
)
from milky_frog.handlers import (
    RunAfterModel,
    RunFailed,
    RunModelChunk,
    RunNotification,
    RunPaused,
)
from milky_frog.handlers.context import HandlerContext
from milky_frog.ui.tui.messages import (
    AddText,
    ApprovalRequired,
    RunError,
    RunNotificationMsg,
    UpdateUsage,
)
from milky_frog.ui.tui.renderer import TextualStreamRenderer

_WORKSPACE = Path("/tmp")


class _MessageQueue:
    def __init__(self) -> None:
        self.messages: list[Message] = []

    def post_message(self, message: Message) -> bool:
        self.messages.append(message)
        return True


async def test_renderer_maps_run_notification() -> None:
    queue = _MessageQueue()
    renderer = TextualStreamRenderer(queue)

    await renderer.on_event(
        RunNotification(run_id="run-1", message="retrying connection", level="warning"),
        HandlerContext(),
    )

    assert len(queue.messages) == 1
    message = queue.messages[0]
    assert isinstance(message, RunNotificationMsg)
    assert message.message == "retrying connection"
    assert message.level == "warning"


async def test_renderer_maps_run_failed_to_run_error() -> None:
    queue = _MessageQueue()
    renderer = TextualStreamRenderer(queue)

    await renderer.on_event(
        RunFailed(
            run_id="run-1",
            error=ConnectionError("offline"),
            state=RunState("run-1", _WORKSPACE),
        ),
        HandlerContext(),
    )

    assert len(queue.messages) == 1
    message = queue.messages[0]
    assert isinstance(message, RunError)
    assert message.error == "ConnectionError: offline"


async def test_renderer_maps_model_chunk_and_approval_pause() -> None:
    queue = _MessageQueue()
    renderer = TextualStreamRenderer(queue)

    await renderer.on_event(
        RunModelChunk(
            run_id="run-1",
            request=ModelRequest((), ()),
            chunk=TextDelta("hi"),
        ),
        HandlerContext(),
    )
    await renderer.on_event(
        RunPaused(
            run_id="run-1",
            status=RunStatus.WAITING_FOR_APPROVAL,
            reason="approval needed for: bash",
            model_calls=1,
            state=RunState("run-1", _WORKSPACE),
        ),
        HandlerContext(),
    )

    assert isinstance(queue.messages[0], AddText)
    assert queue.messages[0].text == "hi"
    assert isinstance(queue.messages[1], ApprovalRequired)
    assert queue.messages[1].reason == "approval needed for: bash"


async def test_renderer_accumulates_usage_on_after_model() -> None:
    queue = _MessageQueue()
    renderer = TextualStreamRenderer(queue)
    request = ModelRequest((), ())

    await renderer.on_event(
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
        ),
        HandlerContext(),
    )

    assert len(queue.messages) == 1
    update = queue.messages[0]
    assert isinstance(update, UpdateUsage)
    assert update.usage.cumulative.input_tokens == 3
    assert update.usage.cumulative.output_tokens == 5
