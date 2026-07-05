from __future__ import annotations

from collections.abc import Callable
from typing import override

from textual.message import Message

from milky_frog.core.handlers import HandlerDeps
from milky_frog.domain import RunResult, RunStatus, RunUsage
from milky_frog.events.events import (
    RunAfterModel,
    RunAfterTool,
    RunBeforeModel,
    RunBeforeTool,
    RunCancelled,
    RunCompaction,
    RunCompleted,
    RunFailed,
    RunModelChunk,
    RunModelReasoning,
    RunNotice,
    RunPaused,
    RunStarted,
)
from milky_frog.events.hub import EventHub, Handler
from milky_frog.tui.messages import (
    AddText,
    AddThinking,
    ApprovalRequired,
    CompactionMsg,
    RunFinished,
    RunNoticeMsg,
    ToolCallMsg,
    ToolResultMsg,
    UpdateUsage,
)

Emit = Callable[[Message], object]


class TuiPresentationHandler(Handler):
    """Lifecycle Handler bundle: maps Harness signals to Textual messages.

    Registered on the shared ``EventHub`` beside checkpointing,
    policy, and observability.  ``MilkyFrogApp`` supplies ``post_message`` as
    the emit target so this bundle stays free of widget types.
    """

    def __init__(self, emit: Emit) -> None:
        self._emit = emit
        self._running = RunUsage()

    @override
    def register(self, hub: EventHub) -> None:
        hub.on(RunStarted)(self._on_started)
        hub.on(RunBeforeModel)(self._on_before_model)
        hub.on(RunModelChunk)(self._on_model_chunk)
        hub.on(RunModelReasoning)(self._on_model_reasoning)
        hub.on(RunAfterModel)(self._on_after_model)
        hub.on(RunBeforeTool)(self._on_before_tool)
        hub.on(RunAfterTool)(self._on_after_tool)
        hub.on(RunCompaction)(self._on_compaction)
        hub.on(RunNotice)(self._on_notice)
        hub.on(RunPaused)(self._on_paused)
        hub.on(RunCompleted)(self._on_terminal)
        hub.on(RunFailed)(self._on_terminal)
        hub.on(RunCancelled)(self._on_terminal)

    async def _on_started(self, event: RunStarted, deps: HandlerDeps | None = None) -> None:
        self._running = RunUsage()

    async def _on_before_model(
        self, event: RunBeforeModel, deps: HandlerDeps | None = None
    ) -> None:
        self._emit(AddThinking(""))

    async def _on_model_chunk(self, event: RunModelChunk, deps: HandlerDeps | None = None) -> None:
        self._emit(AddText(event.chunk.content))

    async def _on_model_reasoning(
        self, event: RunModelReasoning, deps: HandlerDeps | None = None
    ) -> None:
        self._emit(AddThinking(event.chunk.content))

    async def _on_after_model(self, event: RunAfterModel, deps: HandlerDeps | None = None) -> None:
        self._running = self._running.record(event.response.usage)
        self._emit(UpdateUsage(self._running))

    async def _on_before_tool(self, event: RunBeforeTool, deps: HandlerDeps | None = None) -> None:
        call = event.call
        self._emit(ToolCallMsg(call.name, call.arguments))

    async def _on_after_tool(self, event: RunAfterTool, deps: HandlerDeps | None = None) -> None:
        if event.call.name == "bash":
            return  # BashRenderHandler handles bash results
        call = event.call
        result = event.result
        self._emit(ToolResultMsg(call.name, content=result.content, is_error=result.is_error))

    async def _on_compaction(self, event: RunCompaction, deps: HandlerDeps | None = None) -> None:
        # The summarization model call never flows through ``after_model``, so fold
        # its token cost into the running total here — otherwise the status bar
        # under-reports what the Run was billed.
        if event.usage.recorded:
            self._running = self._running.record(event.usage)
            self._emit(UpdateUsage(self._running))
        self._emit(CompactionMsg(event.messages_folded))

    async def _on_notice(self, event: RunNotice, deps: HandlerDeps | None = None) -> None:
        self._emit(RunNoticeMsg(event.message, level=event.level))

    async def _on_paused(self, event: RunPaused, deps: HandlerDeps | None = None) -> None:
        result = event.result
        if result.status is RunStatus.WAITING_FOR_APPROVAL:
            # Extract tool name from the pending assistant message.
            tool_name = ""
            for msg in reversed(event.state.messages):
                if msg.role.value == "assistant" and msg.tool_calls:
                    tool_name = msg.tool_calls[0].name
                    break
            self._emit(ApprovalRequired(result.run_id, result.final_message, tool_name))
            return
        self._emit(_run_finished(result))

    async def _on_terminal(
        self,
        event: RunCompleted | RunFailed | RunCancelled,
        deps: HandlerDeps | None = None,
    ) -> None:
        self._emit(_run_finished(event.result))


def _run_finished(result: RunResult) -> RunFinished:
    return RunFinished(
        result=result,
        status=result.status,
        message=result.final_message,
    )
