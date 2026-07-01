from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Literal, Self, override

from langfuse import Langfuse
from langfuse.types import TraceContext

from milky_frog.core.handlers import HandlerDeps
from milky_frog.events.events import (
    BaseEvent,
    NoticeLevel,
    RunAfterModel,
    RunAfterTool,
    RunBeforeModel,
    RunBeforeResume,
    RunBeforeStart,
    RunBeforeTool,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunModelChunk,
    RunModelReasoning,
    RunNotice,
    RunPaused,
    RunStarted,
    RunTurnEnd,
    RunTurnStart,
)
from milky_frog.events.hub import EventHub, Handler
from milky_frog.settings import LangfuseSettings, Settings

logger = logging.getLogger(__name__)

LangfuseLevel = Literal["DEBUG", "DEFAULT", "WARNING", "ERROR"]

_NOTICE_LEVELS: dict[NoticeLevel, LangfuseLevel] = {
    "info": "DEFAULT",
    "warning": "WARNING",
    "error": "ERROR",
}


class LangfuseHandler(Handler):
    """Records each Run as a Langfuse trace with generation and tool spans."""

    @classmethod
    def from_settings(cls, settings: Settings) -> LangfuseHandler | None:
        if not settings.langfuse.active:
            return None
        return cls(settings.langfuse)

    def __init__(self, settings: LangfuseSettings) -> None:
        self._settings = settings
        self._client: Langfuse | None = None
        self._trace_ids: dict[str, str] = {}
        self._generations: dict[str, Any] = {}
        self._tool_spans: dict[str, Any] = {}
        self._turn_spans: dict[str, Any] = {}
        self._stream_text: dict[str, str] = {}
        self._stream_reasoning: dict[str, str] = {}
        self._flush_task: asyncio.Task[None] | None = None

    @override
    def register(self, hub: EventHub) -> None:
        hub.on(RunBeforeStart)(self._before_start)
        hub.on(RunBeforeResume)(self._before_resume)
        hub.on(RunStarted)(self._run_started)
        hub.on(RunCompleted)(self._on_terminal)
        hub.on(RunCancelled)(self._on_terminal)
        hub.on(RunPaused)(self._on_terminal)
        hub.on(RunFailed)(self._on_terminal)
        hub.on(RunBeforeModel)(self._before_model)
        hub.on(RunModelChunk)(self._model_chunk)
        hub.on(RunModelReasoning)(self._model_reasoning)
        hub.on(RunAfterModel)(self._after_model)
        hub.on(RunBeforeTool)(self._before_tool)
        hub.on(RunAfterTool)(self._after_tool)
        hub.on(RunTurnStart)(self._turn_start)
        hub.on(RunTurnEnd)(self._turn_end)
        hub.on(RunNotice)(self._run_notice)

    @override
    async def __aenter__(self) -> Self:
        if self._client is None:
            self._client = Langfuse(
                public_key=self._settings.public_key,
                secret_key=self._settings.secret_key,
                base_url=self._settings.host,
            )
        return self

    @override
    async def aclose(self) -> None:
        if self._client is None:
            return
        await self._drain_flush()
        await self._flush_client()
        shutdown = getattr(self._client, "shutdown", None)
        if callable(shutdown):
            with contextlib.suppress(Exception):
                await asyncio.to_thread(shutdown)
        self._client = None

    async def _flush_client(self) -> None:
        client = self._client
        if client is None:
            return
        timeout = self._settings.flush_timeout_seconds
        try:
            await asyncio.wait_for(
                asyncio.to_thread(client.flush),
                timeout=timeout,
            )
        except TimeoutError:
            logger.warning("Langfuse flush timed out after %gs", timeout)
        except Exception:
            logger.exception("Langfuse flush error")

    def _schedule_flush(self) -> None:
        if self._client is None:
            return
        task = self._flush_task
        if task is not None and not task.done():
            return
        self._flush_task = asyncio.create_task(self._flush_client())

    async def _drain_flush(self) -> None:
        task = self._flush_task
        if task is None:
            return
        with contextlib.suppress(Exception):
            await task
        self._flush_task = None

    @property
    def _langfuse_client(self) -> Langfuse:
        if self._client is None:
            msg = "LangfuseHandler must be entered before use"
            raise RuntimeError(msg)
        return self._client

    # ── Run lifecycle ────────────────────────────────────────────────

    async def _before_start(self, event: RunBeforeStart, deps: HandlerDeps | None = None) -> None:
        try:
            trace_id = self._trace_ids.setdefault(
                event.run_id, self._langfuse_client.create_trace_id()
            )
            self._langfuse_client.start_observation(
                trace_context=TraceContext(trace_id=trace_id),
                name="run_before_start",
                as_type="span",
                input={
                    "prompt": event.request.prompt,
                    "workspace": str(event.workspace),
                },
            ).end()
        except Exception:
            logger.exception("Langfuse before_start error")

    async def _before_resume(self, event: RunBeforeResume, deps: HandlerDeps | None = None) -> None:
        try:
            trace_id = self._trace_ids.setdefault(
                event.run_id, self._langfuse_client.create_trace_id()
            )
            self._langfuse_client.start_observation(
                trace_context=TraceContext(trace_id=trace_id),
                name="run_before_resume",
                as_type="span",
                input={
                    "prompt": event.prompt,
                    "stored_status": event.stored_status.value,
                },
            ).end()
        except Exception:
            logger.exception("Langfuse before_resume error")

    async def _run_started(self, event: RunStarted, deps: HandlerDeps | None = None) -> None:
        try:
            self._trace_ids.setdefault(event.run_id, self._langfuse_client.create_trace_id())
        except Exception:
            logger.exception("Langfuse run_started error")

    async def _on_terminal(self, event: BaseEvent, deps: HandlerDeps | None = None) -> None:
        try:
            self._end_run(event)
        except Exception:
            logger.exception("Langfuse terminal error: %s", type(event).__name__)
        finally:
            self._cleanup_run(event.run_id)
            self._schedule_flush()

    def _end_run(self, event: BaseEvent) -> None:
        trace_id = self._trace_ids.get(event.run_id)
        if trace_id is None:
            return
        ctx = TraceContext(trace_id=trace_id)
        if isinstance(event, RunCompleted):
            self._langfuse_client.start_observation(
                trace_context=ctx,
                name="run_completed",
                as_type="span",
                output=event.result.final_message,
            ).end()
        elif isinstance(event, RunFailed):
            self._langfuse_client.start_observation(
                trace_context=ctx,
                name="run_failed",
                as_type="span",
                level="ERROR",
                status_message=event.result.final_message,
            ).end()
        elif isinstance(event, RunCancelled):
            self._langfuse_client.start_observation(
                trace_context=ctx,
                name="run_cancelled",
                as_type="span",
                level="WARNING",
                status_message=event.result.final_message,
            ).end()
        elif isinstance(event, RunPaused):
            self._langfuse_client.start_observation(
                trace_context=ctx,
                name="run_paused",
                as_type="span",
                level="WARNING",
                status_message=event.result.final_message,
            ).end()

    # ── Turn lifecycle ─────────────────────────────────────────────

    async def _turn_start(self, event: RunTurnStart, deps: HandlerDeps | None = None) -> None:
        try:
            trace_id = self._trace_ids.get(event.run_id)
            if trace_id:
                self._turn_spans[event.run_id] = self._langfuse_client.start_observation(
                    trace_context=TraceContext(trace_id=trace_id),
                    name=f"turn_{event.model_call}",
                    as_type="span",
                )
        except Exception:
            logger.exception("Langfuse turn_start error")

    async def _turn_end(self, event: RunTurnEnd, deps: HandlerDeps | None = None) -> None:
        try:
            span = self._turn_spans.pop(event.run_id, None)
            if span:
                span.end()
        except Exception:
            logger.exception("Langfuse turn_end error")

    # ── Model calls ──────────────────────────────────────────────────

    async def _model_chunk(self, event: RunModelChunk, deps: HandlerDeps | None = None) -> None:
        try:
            accumulated = self._stream_text.get(event.run_id, "") + event.chunk.content
            self._stream_text[event.run_id] = accumulated
            gen = self._generations.get(event.run_id)
            if gen is not None:
                gen.update(output=accumulated)
        except Exception:
            logger.exception("Langfuse model_chunk error")

    async def _model_reasoning(
        self, event: RunModelReasoning, deps: HandlerDeps | None = None
    ) -> None:
        try:
            accumulated = self._stream_reasoning.get(event.run_id, "") + event.chunk.content
            self._stream_reasoning[event.run_id] = accumulated
            gen = self._generations.get(event.run_id)
            if gen is not None:
                gen.update(metadata={"reasoning": accumulated})
        except Exception:
            logger.exception("Langfuse model_reasoning error")

    async def _before_model(self, event: RunBeforeModel, deps: HandlerDeps | None = None) -> None:
        try:
            trace_id = self._trace_ids.setdefault(
                event.run_id, self._langfuse_client.create_trace_id()
            )
            self._generations[event.run_id] = self._langfuse_client.start_observation(
                trace_context=TraceContext(trace_id=trace_id),
                name="model_call",
                as_type="generation",
                input=[
                    {"role": m.role.value, "content": m.content} for m in event.request.messages
                ],
            )
        except Exception:
            logger.exception("Langfuse before_model error")

    async def _after_model(self, event: RunAfterModel, deps: HandlerDeps | None = None) -> None:
        try:
            gen = self._generations.pop(event.run_id, None)
            self._stream_text.pop(event.run_id, None)
            self._stream_reasoning.pop(event.run_id, None)
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

    async def _run_notice(self, event: RunNotice, deps: HandlerDeps | None = None) -> None:
        try:
            trace_id = self._trace_ids.get(event.run_id)
            if trace_id is None:
                return
            self._langfuse_client.start_observation(
                trace_context=TraceContext(trace_id=trace_id),
                name="run_notice",
                as_type="span",
                level=_NOTICE_LEVELS[event.level],
                status_message=event.message,
            ).end()
        except Exception:
            logger.exception("Langfuse run_notice error")

    # ── Tool calls ───────────────────────────────────────────────────

    async def _before_tool(self, event: RunBeforeTool, deps: HandlerDeps | None = None) -> None:
        try:
            trace_id = self._trace_ids.get(event.run_id)
            if trace_id:
                key = f"{event.run_id}:{event.call.id}"
                self._tool_spans[key] = self._langfuse_client.start_observation(
                    trace_context=TraceContext(trace_id=trace_id),
                    name=event.call.name,
                    as_type="tool",
                    input=event.call.arguments,
                )
        except Exception:
            logger.exception("Langfuse before_tool error")

    async def _after_tool(self, event: RunAfterTool, deps: HandlerDeps | None = None) -> None:
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
        self._stream_text.pop(run_id, None)
        self._stream_reasoning.pop(run_id, None)
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
