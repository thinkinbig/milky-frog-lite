from __future__ import annotations

from textual.app import App

from milky_frog.domain import RunUsage
from milky_frog.handlers import (
    BaseHandler,
    LifecycleBus,
    RunAfterModel,
    RunAfterTool,
    RunBeforeTool,
    RunModelChunk,
    RunModelReasoning,
    RunStarted,
)
from milky_frog.ui.tui.messages import (
    AddText,
    AddThinking,
    ToolCallMsg,
    ToolResultMsg,
    UpdateUsage,
)


class TuiStreamingHandlers(BaseHandler):
    """LifecycleBus handlers that stream model output to a Textual app.

    One instance per session. Listens to Harness lifecycle events and
    posts custom Textual messages that the app's message handlers render
    in the conversation view.
    """

    def __init__(self, app: App[None]) -> None:
        """Wrap a Textual App whose ``post_message`` delivers lifecycle updates.

        The ``app`` parameter expects a ``textual.app.App``-like object (or a
        ``MilkyFrogApp`` instance) that defines the message handler methods
        matching the messages in ``milky_frog.ui.tui.messages``.
        """
        self._app = app
        self._running = RunUsage()

    def register(self, registry: LifecycleBus) -> None:
        registry.on(RunStarted)(self._reset_usage)
        registry.on(RunModelReasoning)(self._print_reasoning)
        registry.on(RunModelChunk)(self._print_chunk)
        registry.on(RunAfterModel)(self._print_usage)
        registry.on(RunBeforeTool)(self._print_tool_call)
        registry.on(RunAfterTool)(self._print_tool_result)

    async def _reset_usage(self, event: RunStarted) -> None:
        del event
        self._running = RunUsage()

    async def _print_reasoning(self, event: RunModelReasoning) -> None:
        self._app.post_message(AddThinking(event.chunk.content))

    async def _print_chunk(self, event: RunModelChunk) -> None:
        self._app.post_message(AddText(event.chunk.content))

    async def _print_usage(self, event: RunAfterModel) -> None:
        self._running = self._running.record(event.response.usage)
        self._app.post_message(UpdateUsage(self._running))

    async def _print_tool_call(self, event: RunBeforeTool) -> None:
        self._app.post_message(ToolCallMsg(event.call.name, event.call.arguments))

    async def _print_tool_result(self, event: RunAfterTool) -> None:
        self._app.post_message(
            ToolResultMsg(
                event.call.name,
                content=event.result.content,
                is_error=event.result.is_error,
            )
        )
