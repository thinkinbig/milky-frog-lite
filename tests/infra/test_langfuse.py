from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from milky_frog.domain import RunRequest, RunResult, RunStatus
from milky_frog.handlers.events import (
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunPaused,
    RunStarted,
)
from milky_frog.handlers.registry import HandlerRegistry
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
def langfuse_handler(monkeypatch: pytest.MonkeyPatch) -> tuple[LangfuseHandler, FakeLangfuseClient]:
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
    return LangfuseHandler(settings), client


def _run_request() -> RunRequest:
    return RunRequest("hello", Path("/workspace"))


@pytest.mark.asyncio
async def test_langfuse_run_started_registers_trace(
    langfuse_handler: tuple[LangfuseHandler, FakeLangfuseClient],
) -> None:
    handler, client = langfuse_handler
    registry = HandlerRegistry()
    handler.register(registry)

    await registry.notify(RunStarted(run_id="run-1", request=_run_request()))

    assert handler._trace_ids["run-1"] == "trace-0"
    assert client.trace_ids == ["trace-0"]


@pytest.mark.asyncio
async def test_langfuse_run_completed_records_span_and_cleans_up(
    langfuse_handler: tuple[LangfuseHandler, FakeLangfuseClient],
) -> None:
    handler, client = langfuse_handler
    registry = HandlerRegistry()
    handler.register(registry)
    await registry.notify(RunStarted(run_id="run-1", request=_run_request()))

    result = RunResult("run-1", RunStatus.COMPLETED, "done", 1)
    await registry.notify(RunCompleted(run_id="run-1", result=result))

    completed = client.observations[-1]
    assert completed.start_kwargs["name"] == "run_completed"
    assert completed.ended is True
    assert "run-1" not in handler._trace_ids


@pytest.mark.asyncio
async def test_langfuse_run_cancelled_records_warning_span_and_cleans_up(
    langfuse_handler: tuple[LangfuseHandler, FakeLangfuseClient],
) -> None:
    handler, client = langfuse_handler
    registry = HandlerRegistry()
    handler.register(registry)
    await registry.notify(RunStarted(run_id="run-1", request=_run_request()))

    await registry.notify(RunCancelled(run_id="run-1", reason="cancelled", model_calls=0))

    cancelled = client.observations[-1]
    assert cancelled.start_kwargs["name"] == "run_cancelled"
    assert cancelled.start_kwargs["level"] == "WARNING"
    assert cancelled.ended is True
    assert "run-1" not in handler._trace_ids


@pytest.mark.asyncio
async def test_langfuse_run_paused_records_warning_span_and_cleans_up(
    langfuse_handler: tuple[LangfuseHandler, FakeLangfuseClient],
) -> None:
    handler, client = langfuse_handler
    registry = HandlerRegistry()
    handler.register(registry)
    await registry.notify(RunStarted(run_id="run-1", request=_run_request()))

    await registry.notify(
        RunPaused(
            run_id="run-1",
            status=RunStatus.PAUSED_LIMIT,
            reason="limit",
            model_calls=1,
        )
    )

    paused = client.observations[-1]
    assert paused.start_kwargs["name"] == "run_paused"
    assert paused.ended is True
    assert "run-1" not in handler._trace_ids


@pytest.mark.asyncio
async def test_langfuse_run_failed_records_error_span_and_cleans_up(
    langfuse_handler: tuple[LangfuseHandler, FakeLangfuseClient],
) -> None:
    handler, client = langfuse_handler
    registry = HandlerRegistry()
    handler.register(registry)
    await registry.notify(RunStarted(run_id="run-1", request=_run_request()))

    await registry.notify(RunFailed(run_id="run-1", error=RuntimeError("boom")))

    failed = client.observations[-1]
    assert failed.start_kwargs["name"] == "run_failed"
    assert failed.start_kwargs["level"] == "ERROR"
    assert failed.ended is True
    assert "run-1" not in handler._trace_ids


@pytest.mark.asyncio
async def test_langfuse_terminal_event_flushes_and_cleans_up(
    langfuse_handler: tuple[LangfuseHandler, FakeLangfuseClient],
) -> None:
    handler, client = langfuse_handler
    registry = HandlerRegistry()
    handler.register(registry)
    await registry.notify(RunStarted(run_id="run-1", request=_run_request()))

    result = RunResult("run-1", RunStatus.COMPLETED, "done", 1)
    await registry.notify(RunCompleted(run_id="run-1", result=result))

    assert client.flushed == 1
    assert "run-1" not in handler._trace_ids


@pytest.mark.asyncio
async def test_langfuse_aclose_flushes_client(
    langfuse_handler: tuple[LangfuseHandler, FakeLangfuseClient],
) -> None:
    handler, client = langfuse_handler

    await handler.aclose()

    assert client.flushed == 1
