from __future__ import annotations

from milky_frog.domain import RunUsage
from milky_frog.handlers import (
    AfterModel,
    BaseHandler,
    HandlerRegistry,
    OnModelChunk,
    OnModelReasoning,
    RunStarted,
)
from milky_frog.ui.streaming import StreamingPrinter
from milky_frog.ui.usage import format_run_usage


class StreamingHandlers(BaseHandler):
    """Streams model reasoning, text, and a running token counter to the console.

    Holds the running-token accumulator as instance state. Runs are strictly
    sequential in the foreground, so one accumulator suffices; it is reset at
    each Run start, and the footer's final total comes from the
    harness-authoritative RunResult.
    """

    def __init__(self, printer: StreamingPrinter) -> None:
        self._printer = printer
        self._running = RunUsage()

    def register(self, registry: HandlerRegistry) -> None:
        registry.on(RunStarted)(self._reset_usage)
        registry.on(OnModelReasoning)(self._print_reasoning)
        registry.on(OnModelChunk)(self._print_chunk)
        registry.on(AfterModel)(self._print_usage)

    async def _reset_usage(self, event: RunStarted) -> None:
        del event
        self._running = RunUsage()

    async def _print_reasoning(self, event: OnModelReasoning) -> None:
        self._printer.on_reasoning(event.chunk.content)

    async def _print_chunk(self, event: OnModelChunk) -> None:
        self._printer.on_delta(event.chunk.content)

    async def _print_usage(self, event: AfterModel) -> None:
        self._running = self._running.record(event.response.usage)
        # Only emit a live line for a turn that continues (has tool calls); the
        # final turn's total is rendered once in the footer, and leaving its
        # stream open lets the loop detect whether anything was streamed.
        summary = format_run_usage(self._running)
        if event.response.tool_calls and summary is not None:
            self._printer.usage(summary)
