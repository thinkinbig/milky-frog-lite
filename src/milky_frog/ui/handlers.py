from __future__ import annotations

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
from milky_frog.ui.streaming import StreamingPrinter
from milky_frog.ui.usage import format_run_usage


class StreamingHandlers(BaseHandler):
    """Streams model reasoning, text, and a running token counter to the console."""

    def __init__(self, printer: StreamingPrinter) -> None:
        self._printer = printer
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
        self._printer.on_reasoning(event.chunk.content)

    async def _print_chunk(self, event: RunModelChunk) -> None:
        self._printer.on_delta(event.chunk.content)

    async def _print_usage(self, event: RunAfterModel) -> None:
        self._running = self._running.record(event.response.usage)
        summary = format_run_usage(self._running)
        if event.response.tool_calls and summary is not None:
            self._printer.usage(summary)

    async def _print_tool_call(self, event: RunBeforeTool) -> None:
        self._printer.tool_call(event.call.name)

    async def _print_tool_result(self, event: RunAfterTool) -> None:
        self._printer.tool_result(is_error=event.result.is_error)
