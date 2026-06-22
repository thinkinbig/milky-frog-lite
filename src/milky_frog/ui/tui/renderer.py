from __future__ import annotations

from textual.app import App

from milky_frog.domain import RunUsage
from milky_frog.handlers import (
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


class TextualStreamRenderer:
    """Framework-internal render pipeline: translates streaming events into
    Textual messages posted to the TUI app.

    Subscribes to the ``LifecycleBus`` via ``subscribe`` so it receives
    every event alongside cross-cutting handlers (observability, policy, …)
    — no direct coupling to the emitter or Harness.
    """

    def __init__(self, app: App[None]) -> None:
        self._app = app
        self._running = RunUsage()

    # ── Bus entry point ───────────────────────────────────────────────

    async def on_event(self, event: object, ctx: object) -> None:
        """Receive every lifecycle signal via ``LifecycleBus.subscribe``."""
        del ctx
        match event:
            case RunStarted():
                self._running = RunUsage()
            case RunModelChunk(chunk=chunk):
                self._app.post_message(AddText(chunk.content))
            case RunModelReasoning(chunk=chunk):
                self._app.post_message(AddThinking(chunk.content))
            case RunAfterModel(response=response):
                self._running = self._running.record(response.usage)
                self._app.post_message(UpdateUsage(self._running))
            case RunBeforeTool(call=call):
                self._app.post_message(ToolCallMsg(call.name, call.arguments))
            case RunAfterTool(call=call, result=result):
                self._app.post_message(
                    ToolResultMsg(
                        call.name,
                        content=result.content,
                        is_error=result.is_error,
                    )
                )
