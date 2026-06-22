from __future__ import annotations

import contextlib
import logging
from typing import Any

from langfuse import Langfuse
from langfuse.types import TraceContext

from milky_frog.handlers.bus import BaseHandler, LifecycleBus
from milky_frog.handlers.context import HandlerContext
from milky_frog.handlers.events import (
    BaseEvent,
    RunAfterModel,
    RunAfterTool,
    RunBeforeModel,
    RunBeforeTool,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunPaused,
    RunStarted,
    RunTurnEnd,
    RunTurnStart,
)
from milky_frog.settings import LangfuseSettings, Settings

logger = logging.getLogger(__name__)


class LangfuseHandler(BaseHandler):
    """Records each Run as a Langfuse trace with generation and tool spans."""

    @classmethod
    def from_settings(cls, settings: Settings) -> LangfuseHandler | None:
        if not settings.langfuse.active:
            return None
        return cls(settings.langfuse)

    def __init__(self, settings: LangfuseSettings) -> None:
        self._client = Langfuse(
            public_key=settings.public_key,
            secret_key=settings.secret_key,
            base_url=settings.host,
        )
        self._trace_ids: dict[str, str] = {}
        self._generations: dict[str, Any] = {}
        self._tool_spans: dict[str, Any] = {}
        self._turn_spans: dict[str, Any] = {}

    def register(self, registry: LifecycleBus) -> None:
        registry.on(RunStarted)(self._run_started)
        registry.on(RunCompleted)(self._on_terminal)
        registry.on(RunCancelled)(self._on_terminal)
        registry.on(RunPaused)(self._on_terminal)
        registry.on(RunFailed)(self._on_terminal)
        registry.on(RunBeforeModel)(self._before_model)
        registry.on(RunAfterModel)(self._after_model)
        registry.on(RunBeforeTool)(self._before_tool)
        registry.on(RunAfterTool)(self._after_tool)
        registry.on(RunTurnStart)(self._turn_start)
        registry.on(RunTurnEnd)(self._turn_end)

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            self._client.flush()
        shutdown = getattr(self._client, "shutdown", None)
        if callable(shutdown):
            with contextlib.suppress(Exception):
                shutdown()

    # ── Run lifecycle ────────────────────────────────────────────────

    async def _run_started(self, event: RunStarted, ctx: HandlerContext) -> None:
        try:
            self._trace_ids.setdefault(event.run_id, self._client.create_trace_id())
        except Exception:
            logger.exception("Langfuse run_started error")

    async def _on_terminal(self, event: BaseEvent, ctx: HandlerContext) -> None:
        try:
            self._end_run(event)
        except Exception:
            logger.exception("Langfuse terminal error: %s", type(event).__name__)
        finally:
            self._cleanup_run(event.run_id)
            with contextlib.suppress(Exception):
                self._client.flush()

    def _end_run(self, event: BaseEvent) -> None:
        trace_id = self._trace_ids.get(event.run_id)
        if trace_id is None:
            return
        ctx = TraceContext(trace_id=trace_id)
        if isinstance(event, RunCompleted):
            self._client.start_observation(
                trace_context=ctx,
                name="run_completed",
                as_type="span",
                output=event.result.final_message,
            ).end()
        elif isinstance(event, RunFailed):
            self._client.start_observation(
                trace_context=ctx,
                name="run_failed",
                as_type="span",
                level="ERROR",
                status_message=f"{type(event.error).__name__}: {event.error}",
            ).end()
        elif isinstance(event, RunCancelled):
            self._client.start_observation(
                trace_context=ctx,
                name="run_cancelled",
                as_type="span",
                level="WARNING",
                status_message=event.reason,
            ).end()
        elif isinstance(event, RunPaused):
            self._client.start_observation(
                trace_context=ctx,
                name="run_paused",
                as_type="span",
                level="WARNING",
                status_message=event.reason,
            ).end()

    # ── Turn lifecycle ─────────────────────────────────────────────

    async def _turn_start(self, event: RunTurnStart, ctx: HandlerContext) -> None:
        try:
            trace_id = self._trace_ids.get(event.run_id)
            if trace_id:
                self._turn_spans[event.run_id] = self._client.start_observation(
                    trace_context=TraceContext(trace_id=trace_id),
                    name=f"turn_{event.model_call}",
                    as_type="span",
                )
        except Exception:
            logger.exception("Langfuse turn_start error")

    async def _turn_end(self, event: RunTurnEnd, ctx: HandlerContext) -> None:
        try:
            span = self._turn_spans.pop(event.run_id, None)
            if span:
                span.end()
        except Exception:
            logger.exception("Langfuse turn_end error")

    # ── Model calls ──────────────────────────────────────────────────

    async def _before_model(self, event: RunBeforeModel, ctx: HandlerContext) -> None:
        try:
            trace_id = self._trace_ids.setdefault(event.run_id, self._client.create_trace_id())
            self._generations[event.run_id] = self._client.start_observation(
                trace_context=TraceContext(trace_id=trace_id),
                name="model_call",
                as_type="generation",
                input=[
                    {"role": m.role.value, "content": m.content} for m in event.request.messages
                ],
            )
        except Exception:
            logger.exception("Langfuse before_model error")

    async def _after_model(self, event: RunAfterModel, ctx: HandlerContext) -> None:
        try:
            gen = self._generations.pop(event.run_id, None)
            if gen:
                reasoning = event.response.reasoning
                gen.update(
                    output=event.response.content,
                    metadata={"reasoning": reasoning} if reasoning else None,
                    model=event.response.model or None,
                    usage_details={
                        "input": event.response.usage.input_tokens,
                        "output": event.response.usage.output_tokens,
                    },
                )
                gen.end()
        except Exception:
            logger.exception("Langfuse after_model error")

    # ── Tool calls ───────────────────────────────────────────────────

    async def _before_tool(self, event: RunBeforeTool, ctx: HandlerContext) -> None:
        try:
            trace_id = self._trace_ids.get(event.run_id)
            if trace_id:
                key = f"{event.run_id}:{event.call.id}"
                self._tool_spans[key] = self._client.start_observation(
                    trace_context=TraceContext(trace_id=trace_id),
                    name=event.call.name,
                    as_type="tool",
                    input=event.call.arguments,
                )
        except Exception:
            logger.exception("Langfuse before_tool error")

    async def _after_tool(self, event: RunAfterTool, ctx: HandlerContext) -> None:
        try:
            key = f"{event.run_id}:{event.call.id}"
            span = self._tool_spans.pop(key, None)
            if span:
                span.update(
                    output=event.result.content,
                    level="ERROR" if event.result.is_error else "DEFAULT",
                )
                span.end()
        except Exception:
            logger.exception("Langfuse after_tool error")

    # ── Bookkeeping ──────────────────────────────────────────────────

    def _cleanup_run(self, run_id: str) -> None:
        self._trace_ids.pop(run_id, None)
        gen = self._generations.pop(run_id, None)
        if gen:
            with contextlib.suppress(Exception):
                gen.end()
        turn = self._turn_spans.pop(run_id, None)
        if turn:
            with contextlib.suppress(Exception):
                turn.end()
        orphaned = [k for k in self._tool_spans if k.startswith(f"{run_id}:")]
        for k in orphaned:
            span = self._tool_spans.pop(k)
            with contextlib.suppress(Exception):
                span.end()
