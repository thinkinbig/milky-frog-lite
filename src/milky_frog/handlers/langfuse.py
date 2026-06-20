from __future__ import annotations

import contextlib
import logging
from typing import Any

from langfuse import Langfuse
from langfuse.types import TraceContext

from milky_frog.handlers.events import (
    AfterModel,
    AfterTool,
    BeforeModel,
    BeforeTool,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunPaused,
    RunStarted,
)
from milky_frog.handlers.registry import HandlerRegistry
from milky_frog.settings import LangfuseSettings

logger = logging.getLogger(__name__)


class LangfuseHandler:
    """Records each Run as a Langfuse trace with generation and tool spans."""

    def __init__(self, settings: LangfuseSettings) -> None:
        self._client = Langfuse(
            public_key=settings.public_key,
            secret_key=settings.secret_key,
            base_url=settings.host,
        )
        self._trace_ids: dict[str, str] = {}
        self._generations: dict[str, Any] = {}
        self._tool_spans: dict[str, Any] = {}

    def register(self, registry: HandlerRegistry) -> None:
        registry.on(RunStarted)(self._run_started)
        registry.on(RunCompleted)(self._run_completed)
        registry.on(RunCancelled)(self._run_cancelled)
        registry.on(RunPaused)(self._run_paused)
        registry.on(BeforeModel)(self._before_model)
        registry.on(AfterModel)(self._after_model)
        registry.on(BeforeTool)(self._before_tool)
        registry.on(AfterTool)(self._after_tool)
        registry.on(RunFailed)(self._run_failed)

    def flush(self) -> None:
        self._client.flush()

    def finalize(self, run_id: str) -> None:
        """Close any open observations for the run, then flush the client."""
        self._cleanup_run(run_id)
        self._client.flush()

    def _cleanup_run(self, run_id: str) -> None:
        self._trace_ids.pop(run_id, None)
        gen = self._generations.pop(run_id, None)
        if gen:
            with contextlib.suppress(Exception):
                gen.end()
        orphaned = [k for k in self._tool_spans if k.startswith(f"{run_id}:")]
        for k in orphaned:
            span = self._tool_spans.pop(k)
            with contextlib.suppress(Exception):
                span.end()

    async def _run_started(self, event: RunStarted) -> None:
        try:
            self._trace_ids.setdefault(event.run_id, self._client.create_trace_id())
        except Exception:
            logger.exception("Langfuse run_started error")

    async def _run_completed(self, event: RunCompleted) -> None:
        try:
            trace_id = self._trace_ids.get(event.run_id)
            if trace_id:
                self._client.start_observation(
                    trace_context=TraceContext(trace_id=trace_id),
                    name="run_completed",
                    as_type="span",
                    output=event.result.final_message,
                ).end()
        except Exception:
            logger.exception("Langfuse run_completed error")
        finally:
            self._cleanup_run(event.run_id)

    async def _run_cancelled(self, event: RunCancelled) -> None:
        try:
            trace_id = self._trace_ids.get(event.run_id)
            if trace_id:
                self._client.start_observation(
                    trace_context=TraceContext(trace_id=trace_id),
                    name="run_cancelled",
                    as_type="span",
                    level="WARNING",
                    status_message=event.reason,
                ).end()
        except Exception:
            logger.exception("Langfuse run_cancelled error")
        finally:
            self._cleanup_run(event.run_id)

    async def _run_paused(self, event: RunPaused) -> None:
        try:
            trace_id = self._trace_ids.get(event.run_id)
            if trace_id:
                self._client.start_observation(
                    trace_context=TraceContext(trace_id=trace_id),
                    name="run_paused",
                    as_type="span",
                    level="WARNING",
                    status_message=event.reason,
                ).end()
        except Exception:
            logger.exception("Langfuse run_paused error")
        finally:
            self._cleanup_run(event.run_id)

    async def _before_model(self, event: BeforeModel) -> None:
        try:
            trace_id = self._trace_ids.setdefault(event.run_id, self._client.create_trace_id())
            gen = self._client.start_observation(
                trace_context=TraceContext(trace_id=trace_id),
                name="model_call",
                as_type="generation",
                input=[
                    {"role": m.role.value, "content": m.content} for m in event.request.messages
                ],
            )
            self._generations[event.run_id] = gen
        except Exception:
            logger.exception("Langfuse before_model error")

    async def _after_model(self, event: AfterModel) -> None:
        try:
            gen = self._generations.pop(event.run_id, None)
            if gen:
                gen.update(
                    output=event.response.content,
                    model=event.response.model or None,
                    usage_details={
                        "input": event.response.usage.get("input_tokens", 0),
                        "output": event.response.usage.get("output_tokens", 0),
                    },
                )
                gen.end()
        except Exception:
            logger.exception("Langfuse after_model error")

    async def _before_tool(self, event: BeforeTool) -> None:
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

    async def _after_tool(self, event: AfterTool) -> None:
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

    async def _run_failed(self, event: RunFailed) -> None:
        try:
            trace_id = self._trace_ids.get(event.run_id)
            if trace_id:
                self._client.start_observation(
                    trace_context=TraceContext(trace_id=trace_id),
                    name="run_failed",
                    as_type="span",
                    level="ERROR",
                    status_message=f"{type(event.error).__name__}: {event.error}",
                ).end()
        except Exception:
            logger.exception("Langfuse run_failed error")
        finally:
            self._cleanup_run(event.run_id)
