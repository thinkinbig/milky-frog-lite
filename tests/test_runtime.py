from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.domain import (
    ModelChunk,
    ModelRequest,
    ModelResponse,
    ResumeError,
    RunStatus,
    StreamDone,
    TextDelta,
)
from milky_frog.handlers import BaseHandler, EventDispatcher, HandlerContext, RunCancelled
from milky_frog.models import OpenAIModel
from milky_frog.runtime import MilkyFrog, MissingModelConfiguration
from milky_frog.settings import LangfuseSettings, Settings
from tests.checkpoint_helpers import run_status, seed_run

_NO_LANGFUSE = LangfuseSettings(
    enabled=False, public_key=None, secret_key=None, host="https://cloud.langfuse.com"
)


def test_milky_frog_runs_through_configured_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    requests: list[ModelRequest] = []

    async def fake_stream(self: OpenAIModel, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del self
        requests.append(request)
        yield StreamDone(ModelResponse(content="done"))

    monkeypatch.setattr(OpenAIModel, "stream", fake_stream)
    settings = Settings(tmp_path, "test-key", "https://example.test", "test-model", _NO_LANGFUSE)

    with MilkyFrog.from_settings(settings) as frog:
        result = frog.run("build it", tmp_path)

    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "done"
    assert requests[0].messages[0].role.value == "system"
    assert requests[0].messages[1].content == "build it"
    assert SqliteCheckpointStore(settings.database_path).get_run(result.run_id) is not None


def test_milky_frog_cancel_stops_foreground_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def slow_stream(self: OpenAIModel, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del self, request
        yield TextDelta("partial")
        await asyncio.sleep(0.05)
        yield StreamDone(ModelResponse(content="done"))

    monkeypatch.setattr(OpenAIModel, "stream", slow_stream)
    settings = Settings(tmp_path, "test-key", "https://example.test", "test-model", _NO_LANGFUSE)
    registry = EventDispatcher()
    cancelled: list[RunCancelled] = []

    @registry.on(RunCancelled)
    async def record(event: RunCancelled, ctx: HandlerContext) -> None:
        del ctx
        cancelled.append(event)

    with MilkyFrog.from_settings(settings, handlers=registry) as frog:

        def request_cancel() -> None:
            time.sleep(0.01)
            frog.cancel()

        cancel_thread = threading.Thread(target=request_cancel)
        cancel_thread.start()
        result = frog.run("slow task", tmp_path)
        cancel_thread.join(timeout=5.0)

    assert result.status is RunStatus.CANCELLED
    assert len(cancelled) == 1
    store = SqliteCheckpointStore(settings.database_path)
    assert run_status(store, result.run_id) is RunStatus.CANCELLED


def test_milky_frog_context_manager_closes_its_bundles(tmp_path: Path) -> None:
    class SpyHandler(BaseHandler):
        def __init__(self) -> None:
            self.closed = 0

        def register(self, registry: EventDispatcher) -> None:
            del registry

        async def aclose(self) -> None:
            self.closed += 1

    spy = SpyHandler()
    settings = Settings(tmp_path, "test-key", None, "test-model", _NO_LANGFUSE)

    with MilkyFrog.from_settings(settings, bundles=[spy]):
        pass

    assert spy.closed == 1


def test_milky_frog_close_isolates_failing_bundle(tmp_path: Path) -> None:
    class FailingHandler(BaseHandler):
        def register(self, registry: EventDispatcher) -> None:
            del registry

        async def aclose(self) -> None:
            raise RuntimeError("boom")

    class SpyHandler(BaseHandler):
        def __init__(self) -> None:
            self.closed = 0

        def register(self, registry: EventDispatcher) -> None:
            del registry

        async def aclose(self) -> None:
            self.closed += 1

    spy = SpyHandler()
    settings = Settings(tmp_path, "test-key", None, "test-model", _NO_LANGFUSE)

    # A failing bundle must neither abort releasing the rest nor escape close().
    with MilkyFrog.from_settings(settings, bundles=[FailingHandler(), spy]):
        pass

    assert spy.closed == 1


def test_milky_frog_close_allows_reuse(tmp_path: Path) -> None:
    settings = Settings(tmp_path, "test-key", None, "test-model", _NO_LANGFUSE)
    frog = MilkyFrog.from_settings(settings)

    # close() must not leave a closed loop behind; the instance stays usable.
    frog.close()

    assert frog._loop is None
    frog.close()  # idempotent
    assert frog._loop is None


def test_milky_frog_rejects_missing_model_configuration(tmp_path: Path) -> None:
    settings = Settings(tmp_path, None, None, None, _NO_LANGFUSE)

    with pytest.raises(MissingModelConfiguration, match="model configuration is missing"):
        MilkyFrog.from_settings(settings)


@pytest.mark.parametrize("api_key,model", [("", "test-model"), ("test-key", ""), ("", "")])
def test_milky_frog_rejects_empty_model_configuration(
    tmp_path: Path, api_key: str, model: str
) -> None:
    settings = Settings(tmp_path, api_key, None, model, _NO_LANGFUSE)

    with pytest.raises(MissingModelConfiguration, match="model configuration is missing"):
        MilkyFrog.from_settings(settings)


def test_milky_frog_resume_advances_stored_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_stream(self: OpenAIModel, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del self, request
        yield StreamDone(ModelResponse(content="resumed"))

    monkeypatch.setattr(OpenAIModel, "stream", fake_stream)
    settings = Settings(tmp_path, "test-key", "https://example.test", "test-model", _NO_LANGFUSE)
    # A Run paused at its model-call limit, persisted before the frog is built.
    store = SqliteCheckpointStore(settings.database_path)
    run_id = "paused-run"
    seed_run(store, run_id, tmp_path, status=RunStatus.PAUSED_LIMIT, final_message="limit")

    with MilkyFrog.from_settings(settings) as frog:
        result = frog.resume(run_id)

    assert result.run_id == run_id
    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "resumed"


def test_milky_frog_resume_rejects_unknown_run(tmp_path: Path) -> None:
    settings = Settings(tmp_path, "test-key", "https://example.test", "test-model", _NO_LANGFUSE)

    with MilkyFrog.from_settings(settings) as frog, pytest.raises(ResumeError, match="unknown Run"):
        frog.resume("does-not-exist")
