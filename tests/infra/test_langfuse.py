from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from milky_frog.domain import (
    ModelRequest,
    ReasoningDelta,
    RunRequest,
    RunResult,
    RunState,
    RunStatus,
    TextDelta,
)
from milky_frog.handlers.dispatcher import EventDispatcher
from milky_frog.handlers.events import (
    RunBeforeModel,
    RunBeforeResume,
    RunBeforeStart,
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
from milky_frog.infra.observability.langfuse import LangfuseHandler
from milky_frog.settings import LangfuseSettings
from tests.stubs import LangfuseClientFactory


class FakeObservation:
    def __init__(self) -> None:
        self.ended = False
        self.updated: dict[str, Any] = {}
        self.start_kwargs: dict[str, Any] = {}

    def update(self, **kwargs: Any) -> FakeObservation:
        self.updated.update(kwargs)
        return self

    def end(self) -> None:
        self.ended = True


class FakeLangfuseClient:
    def __init__(self) -> None:
        self.trace_ids: list[str] = []
        self.observations: list[FakeObservation] = []
        self.flushed = 0

    def create_trace_id(self) -> str:
        trace_id = f"trace-{len(self.trace_ids)}"
        self.trace_ids.append(trace_id)
        return trace_id

    def start_observation(self, **kwargs: Any) -> FakeObservation:
        observation = FakeObservation()
        observation.start_kwargs = kwargs
        self.observations.append(observation)
        return observation

    def flush(self) -> None:
        self.flushed += 1


@pytest.fixture
async def langfuse_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[LangfuseHandler, FakeLangfuseClient]]:
    client = FakeLangfuseClient()
    monkeypatch.setattr(
        "milky_frog.infra.observability.langfuse.Langfuse",
        LangfuseClientFactory(client),
    )
    settings = LangfuseSettings(
        enabled=True,
        public_key="public",
        secret_key="secret",
        host="https://langfuse.test",
    )
    handler = LangfuseHandler(settings)
    await handler.__aenter__()
    yield handler, client
    await handler.__aexit__(None, None, None)


def _run_request() -> RunRequest:
    return RunRequest("hello", Path("/workspace"))


def _run_state(run_id: str = "run-1") -> RunState:
    return RunState(run_id=run_id, workspace=Path("/workspace"))


@pytest.mark.asyncio
async def test_langfuse_run_started_registers_trace(
    langfuse_handler: tuple[LangfuseHandler, FakeLangfuseClient],
) -> None:
    handler, client = langfuse_handler
    registry = EventDispatcher()
    handler.register(registry)

    await registry.notify(RunStarted(run_id="run-1", request=_run_request(), state=_run_state()))

    assert handler._trace_ids["run-1"] == "trace-0"
    assert client.trace_ids == ["trace-0"]


@pytest.mark.asyncio
async def test_langfuse_run_completed_records_span_and_cleans_up(
    langfuse_handler: tuple[LangfuseHandler, FakeLangfuseClient],
) -> None:
    handler, client = langfuse_handler
    registry = EventDispatcher()
    handler.register(registry)
    await registry.notify(RunStarted(run_id="run-1", request=_run_request(), state=_run_state()))

    result = RunResult("run-1", RunStatus.COMPLETED, "done", 1)
    await registry.notify(RunCompleted(run_id="run-1", result=result, state=_run_state()))

    completed = client.observations[-1]
    assert completed.start_kwargs["name"] == "run_completed"
    assert completed.ended is True
    assert "run-1" not in handler._trace_ids


@pytest.mark.asyncio
async def test_langfuse_run_cancelled_records_warning_span_and_cleans_up(
    langfuse_handler: tuple[LangfuseHandler, FakeLangfuseClient],
) -> None:
    handler, client = langfuse_handler
    registry = EventDispatcher()
    handler.register(registry)
    await registry.notify(RunStarted(run_id="run-1", request=_run_request(), state=_run_state()))

    await registry.notify(
        RunCancelled(
            run_id="run-1",
            result=RunResult("run-1", RunStatus.CANCELLED, "cancelled", 0),
            state=_run_state(),
        )
    )

    cancelled = client.observations[-1]
    assert cancelled.start_kwargs["name"] == "run_cancelled"
    assert cancelled.start_kwargs["level"] == "WARNING"
    assert cancelled.start_kwargs["status_message"] == "cancelled"
    assert cancelled.ended is True
    assert "run-1" not in handler._trace_ids


@pytest.mark.asyncio
async def test_langfuse_run_paused_records_warning_span_and_cleans_up(
    langfuse_handler: tuple[LangfuseHandler, FakeLangfuseClient],
) -> None:
    handler, client = langfuse_handler
    registry = EventDispatcher()
    handler.register(registry)
    await registry.notify(RunStarted(run_id="run-1", request=_run_request(), state=_run_state()))

    await registry.notify(
        RunPaused(
            run_id="run-1",
            result=RunResult("run-1", RunStatus.PAUSED_LIMIT, "limit", 1),
            state=_run_state(),
        )
    )

    paused = client.observations[-1]
    assert paused.start_kwargs["name"] == "run_paused"
    assert paused.start_kwargs["status_message"] == "limit"
    assert paused.ended is True
    assert "run-1" not in handler._trace_ids


@pytest.mark.asyncio
async def test_langfuse_run_failed_records_error_span_and_cleans_up(
    langfuse_handler: tuple[LangfuseHandler, FakeLangfuseClient],
) -> None:
    handler, client = langfuse_handler
    registry = EventDispatcher()
    handler.register(registry)
    await registry.notify(RunStarted(run_id="run-1", request=_run_request(), state=_run_state()))

    failed_result = RunResult("run-1", RunStatus.FAILED, "RuntimeError: boom", 0)
    await registry.notify(RunFailed(run_id="run-1", result=failed_result, state=_run_state()))

    failed = client.observations[-1]
    assert failed.start_kwargs["name"] == "run_failed"
    assert failed.start_kwargs["level"] == "ERROR"
    assert failed.start_kwargs["status_message"] == "RuntimeError: boom"
    assert failed.ended is True
    assert "run-1" not in handler._trace_ids


@pytest.mark.asyncio
async def test_langfuse_terminal_event_flushes_and_cleans_up(
    langfuse_handler: tuple[LangfuseHandler, FakeLangfuseClient],
) -> None:
    handler, client = langfuse_handler
    registry = EventDispatcher()
    handler.register(registry)
    await registry.notify(RunStarted(run_id="run-1", request=_run_request(), state=_run_state()))

    result = RunResult("run-1", RunStatus.COMPLETED, "done", 1)
    await registry.notify(RunCompleted(run_id="run-1", result=result, state=_run_state()))

    assert client.flushed == 1
    assert "run-1" not in handler._trace_ids


@pytest.mark.asyncio
async def test_langfuse_aclose_flushes_client(
    langfuse_handler: tuple[LangfuseHandler, FakeLangfuseClient],
) -> None:
    handler, client = langfuse_handler

    await handler.aclose()

    assert client.flushed == 1


@pytest.mark.asyncio
async def test_langfuse_turn_events_create_and_end_span(
    langfuse_handler: tuple[LangfuseHandler, FakeLangfuseClient],
) -> None:
    handler, client = langfuse_handler
    registry = EventDispatcher()
    handler.register(registry)
    await registry.notify(RunStarted(run_id="run-1", request=_run_request(), state=_run_state()))

    await registry.notify(RunTurnStart(run_id="run-1", model_call=1))
    await registry.notify(RunTurnEnd(run_id="run-1", model_call=1))

    spans = [o for o in client.observations if o.start_kwargs.get("as_type") == "span"]
    turn = [s for s in spans if s.start_kwargs.get("name") == "turn_1"]
    assert len(turn) == 1
    assert turn[0].ended is True


@pytest.mark.asyncio
async def test_langfuse_turn_span_nests_model_and_tool(
    langfuse_handler: tuple[LangfuseHandler, FakeLangfuseClient],
) -> None:
    """Turn span opens at RunTurnStart and closes at RunTurnEnd."""
    handler, client = langfuse_handler
    registry = EventDispatcher()
    handler.register(registry)
    await registry.notify(RunStarted(run_id="run-1", request=_run_request(), state=_run_state()))

    await registry.notify(RunTurnStart(run_id="run-1", model_call=1))
    turn_span = client.observations[-1]
    assert turn_span.ended is False

    await registry.notify(RunTurnEnd(run_id="run-1", model_call=1))
    assert turn_span.ended is True


@pytest.mark.asyncio
async def test_langfuse_before_start_registers_trace_and_span(
    langfuse_handler: tuple[LangfuseHandler, FakeLangfuseClient],
) -> None:
    handler, client = langfuse_handler
    registry = EventDispatcher()
    handler.register(registry)

    await registry.notify(
        RunBeforeStart(run_id="run-1", request=_run_request(), workspace=Path("/workspace"))
    )

    assert handler._trace_ids["run-1"] == "trace-0"
    span = client.observations[-1]
    assert span.start_kwargs["name"] == "run_before_start"
    assert span.start_kwargs["input"] == {"prompt": "hello", "workspace": "/workspace"}
    assert span.ended is True


@pytest.mark.asyncio
async def test_langfuse_before_resume_registers_trace_and_span(
    langfuse_handler: tuple[LangfuseHandler, FakeLangfuseClient],
) -> None:
    handler, client = langfuse_handler
    registry = EventDispatcher()
    handler.register(registry)

    await registry.notify(
        RunBeforeResume(
            run_id="run-1",
            prompt="continue",
            stored_status=RunStatus.WAITING_FOR_INPUT,
        )
    )

    assert handler._trace_ids["run-1"] == "trace-0"
    span = client.observations[-1]
    assert span.start_kwargs["name"] == "run_before_resume"
    assert span.start_kwargs["input"] == {
        "prompt": "continue",
        "stored_status": "waiting_for_input",
    }
    assert span.ended is True


@pytest.mark.asyncio
async def test_langfuse_model_chunk_updates_generation_incrementally(
    langfuse_handler: tuple[LangfuseHandler, FakeLangfuseClient],
) -> None:
    handler, client = langfuse_handler
    registry = EventDispatcher()
    handler.register(registry)
    await registry.notify(RunStarted(run_id="run-1", request=_run_request(), state=_run_state()))
    request = ModelRequest(messages=(), tools=())
    await registry.notify(RunBeforeModel(run_id="run-1", request=request))

    await registry.notify(
        RunModelChunk(run_id="run-1", request=request, chunk=TextDelta(content="hel"))
    )
    await registry.notify(
        RunModelChunk(run_id="run-1", request=request, chunk=TextDelta(content="lo"))
    )

    generation = client.observations[-1]
    assert generation.start_kwargs["as_type"] == "generation"
    assert generation.updated["output"] == "hello"


@pytest.mark.asyncio
async def test_langfuse_model_reasoning_updates_generation_metadata(
    langfuse_handler: tuple[LangfuseHandler, FakeLangfuseClient],
) -> None:
    handler, client = langfuse_handler
    registry = EventDispatcher()
    handler.register(registry)
    await registry.notify(RunStarted(run_id="run-1", request=_run_request(), state=_run_state()))
    request = ModelRequest(messages=(), tools=())
    await registry.notify(RunBeforeModel(run_id="run-1", request=request))

    await registry.notify(
        RunModelReasoning(run_id="run-1", request=request, chunk=ReasoningDelta(content="think"))
    )

    generation = client.observations[-1]
    assert generation.updated["metadata"] == {"reasoning": "think"}


@pytest.mark.asyncio
async def test_langfuse_run_notice_records_event(
    langfuse_handler: tuple[LangfuseHandler, FakeLangfuseClient],
) -> None:
    handler, client = langfuse_handler
    registry = EventDispatcher()
    handler.register(registry)
    await registry.notify(RunStarted(run_id="run-1", request=_run_request(), state=_run_state()))

    await registry.notify(RunNotice(run_id="run-1", message="retrying connection", level="warning"))

    notice = client.observations[-1]
    assert notice.start_kwargs["name"] == "run_notice"
    assert notice.start_kwargs["as_type"] == "span"
    assert notice.start_kwargs["level"] == "WARNING"
    assert notice.start_kwargs["status_message"] == "retrying connection"
    assert notice.ended is True
