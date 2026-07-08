from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from milky_frog.adapters.docker import DockerSandboxFactory
from milky_frog.adapters.local import LocalSandbox
from milky_frog.app.session import AgentSession, InactiveAgentSession, MissingModelConfiguration
from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.core.runtime.assemble import make_sandbox_factory
from milky_frog.domain import (
    ApprovalDecision,
    ApprovalVerdict,
    ModelChunk,
    ModelRequest,
    ModelResponse,
    ResumeError,
    RunStatus,
    StreamDone,
    TextDelta,
    ToolCall,
)
from milky_frog.events import EventHub, Handler, RunCancelled
from milky_frog.harness.harness import AgentHarness
from milky_frog.models import OpenAIModel
from milky_frog.project import ProjectConfig, SandboxConfig
from milky_frog.settings import Settings
from tests.checkpoint_helpers import run_status, seed_interrupted_tool_run, seed_run


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "home": tmp_path,
        "api_key": "test-key",
        "model": "test-model",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.asyncio
async def test_session_runs_through_configured_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    requests: list[ModelRequest] = []

    async def fake_stream(self: OpenAIModel, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del self
        requests.append(request)
        yield StreamDone(ModelResponse(content="done"))

    monkeypatch.setattr(OpenAIModel, "stream", fake_stream)
    settings = _settings(
        tmp_path,
        base_url="https://example.test",
    )

    async with AgentSession.from_settings(settings) as session:
        result = await session.start_new("build it", tmp_path)

    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "done"
    assert requests[0].messages[0].role.value == "system"
    assert requests[0].messages[1].content == "build it"
    assert SqliteCheckpointStore(settings.database_path).get_run(result.run_id) is not None


@pytest.mark.asyncio
async def test_session_cancel_stops_foreground_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def slow_stream(self: OpenAIModel, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del self, request
        yield TextDelta("partial")
        await asyncio.sleep(0.05)
        yield StreamDone(ModelResponse(content="done"))

    monkeypatch.setattr(OpenAIModel, "stream", slow_stream)
    settings = _settings(
        tmp_path,
        base_url="https://example.test",
    )
    hub = EventHub()
    cancelled: list[RunCancelled] = []

    @hub.on(RunCancelled)
    async def record(event: RunCancelled, _ctx=None) -> None:
        cancelled.append(event)

    async with AgentSession.from_settings(settings, hub=hub) as session:
        result = await asyncio.gather(
            session.start_new("slow task", tmp_path),
            _async_cancel(session, delay=0.01),
        )
        result = result[0]

    assert result.status is RunStatus.CANCELLED
    assert len(cancelled) == 1
    store = SqliteCheckpointStore(settings.database_path)
    assert run_status(store, result.run_id) is RunStatus.CANCELLED


async def _async_cancel(agent_session: AgentSession, delay: float) -> None:
    await asyncio.sleep(delay)
    agent_session.cancel()


@pytest.mark.asyncio
async def test_session_context_manager_closes_its_bundles(tmp_path: Path) -> None:
    class SpyHandler(Handler):
        def __init__(self) -> None:
            self.closed = 0

        def register(self, hub: EventHub) -> None:
            del hub

        async def aclose(self) -> None:
            self.closed += 1

    spy = SpyHandler()
    settings = _settings(tmp_path)

    async with AgentSession.from_settings(settings, bundles=[spy]):
        pass

    assert spy.closed == 1


@pytest.mark.asyncio
async def test_session_close_isolates_failing_bundle(tmp_path: Path) -> None:
    class FailingHandler(Handler):
        def register(self, hub: EventHub) -> None:
            del hub

        async def aclose(self) -> None:
            raise RuntimeError("boom")

    class SpyHandler(Handler):
        def __init__(self) -> None:
            self.closed = 0

        def register(self, hub: EventHub) -> None:
            del hub

        async def aclose(self) -> None:
            self.closed += 1

    spy = SpyHandler()
    settings = _settings(tmp_path)

    async with AgentSession.from_settings(settings, bundles=[FailingHandler(), spy]):
        pass

    assert spy.closed == 1


@pytest.mark.asyncio
async def test_session_exit_is_idempotent(tmp_path: Path) -> None:
    """Double __aexit__ must not raise."""
    settings = _settings(tmp_path)
    session = AgentSession.from_settings(settings)

    await session.__aenter__()
    await session.__aexit__(None, None, None)
    # Second exit — no-op (resources already released).
    await session.__aexit__(None, None, None)


def test_session_rejects_missing_model_configuration(tmp_path: Path) -> None:
    settings = _settings(tmp_path, api_key=None, model=None)

    with pytest.raises(MissingModelConfiguration, match="model configuration is missing"):
        AgentSession.from_settings(settings)


@pytest.mark.parametrize("api_key,model", [("", "test-model"), ("test-key", ""), ("", "")])
def test_session_rejects_empty_model_configuration(
    tmp_path: Path, api_key: str, model: str
) -> None:
    # Empty strings are coerced to None by the pydantic validator.
    settings = _settings(tmp_path, api_key=api_key, model=model)

    with pytest.raises(MissingModelConfiguration, match="model configuration is missing"):
        AgentSession.from_settings(settings)


@pytest.mark.asyncio
async def test_session_resume_advances_stored_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_stream(self: OpenAIModel, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del self, request
        yield StreamDone(ModelResponse(content="resumed"))

    monkeypatch.setattr(OpenAIModel, "stream", fake_stream)
    settings = _settings(
        tmp_path,
        base_url="https://example.test",
    )
    store = SqliteCheckpointStore(settings.database_path)
    run_id = "paused-run"
    seed_run(store, run_id, tmp_path, status=RunStatus.PAUSED_LIMIT, final_message="limit")

    async with AgentSession.from_settings(settings) as session:
        result = await session.continue_with(run_id)

    assert result.run_id == run_id
    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "resumed"


@pytest.mark.asyncio
async def test_session_resume_resurfaces_waiting_for_approval(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        base_url="https://example.test",
    )
    store = SqliteCheckpointStore(settings.database_path)
    run_id = "approval-run"
    seed_interrupted_tool_run(
        store,
        run_id,
        tmp_path,
        status=RunStatus.WAITING_FOR_APPROVAL,
        final_message="approval needed",
    )

    async with AgentSession.from_settings(settings) as session:
        result = await session.continue_with(run_id)

    assert result.run_id == run_id
    assert result.status is RunStatus.WAITING_FOR_APPROVAL
    assert "echo" in result.final_message


@pytest.mark.asyncio
async def test_session_resume_rejects_unknown_run(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        base_url="https://example.test",
    )

    async with AgentSession.from_settings(settings) as session:
        with pytest.raises(ResumeError, match="unknown Run"):
            await session.continue_with("does-not-exist")


@pytest.mark.asyncio
async def test_session_respond_approval_executes_pending_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_stream(self: OpenAIModel, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del self, request
        yield StreamDone(ModelResponse(content="done"))

    monkeypatch.setattr(OpenAIModel, "stream", fake_stream)
    settings = _settings(
        tmp_path,
        base_url="https://example.test",
    )
    (tmp_path / "note.txt").write_text("hi", encoding="utf-8")
    store = SqliteCheckpointStore(settings.database_path)
    run_id = "approval-run"
    seed_interrupted_tool_run(
        store,
        run_id,
        tmp_path,
        tool_call=ToolCall("call-1", "read_file", {"path": "note.txt"}),
        status=RunStatus.WAITING_FOR_APPROVAL,
        final_message="approval needed",
    )

    async with AgentSession.from_settings(settings) as session:
        result = await session.respond_approval(run_id, ApprovalVerdict(ApprovalDecision.APPROVE))

    assert result.run_id == run_id
    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "done"


@pytest.mark.asyncio
async def test_session_persists_cancel_on_interrupt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def interrupted_resume(*_args: object, **_kwargs: object) -> object:
        raise asyncio.CancelledError()

    monkeypatch.setattr(AgentHarness, "resume", interrupted_resume)
    settings = _settings(
        tmp_path,
        base_url="https://example.test",
    )
    store = SqliteCheckpointStore(settings.database_path)
    run_id = "running-run"
    seed_run(store, run_id, tmp_path, status=RunStatus.RUNNING)

    async with AgentSession.from_settings(settings) as session:
        with pytest.raises(asyncio.CancelledError):
            await session.continue_with(run_id)

    assert run_status(store, run_id) is RunStatus.CANCELLED


@pytest.mark.asyncio
async def test_session_persists_cancel_on_exit_while_busy(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        base_url="https://example.test",
    )
    store = SqliteCheckpointStore(settings.database_path)
    run_id = "running-run"
    seed_run(store, run_id, tmp_path, status=RunStatus.RUNNING)

    session = await AgentSession.from_settings(settings).__aenter__()
    try:
        session.busy = True
        session.run_id = run_id
    finally:
        await session.__aexit__(None, None, None)

    assert run_status(store, run_id) is RunStatus.CANCELLED


@pytest.mark.asyncio
async def test_session_exit_leaves_waiting_for_approval_unchanged(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        base_url="https://example.test",
    )
    store = SqliteCheckpointStore(settings.database_path)
    run_id = "approval-run"
    seed_interrupted_tool_run(
        store,
        run_id,
        tmp_path,
        status=RunStatus.WAITING_FOR_APPROVAL,
        final_message="approval needed",
    )

    session = await AgentSession.from_settings(settings).__aenter__()
    try:
        session.busy = True
        session.run_id = run_id
    finally:
        await session.__aexit__(None, None, None)

    assert run_status(store, run_id) is RunStatus.WAITING_FOR_APPROVAL


@pytest.mark.asyncio
async def test_session_requires_enter_before_checkpoints(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    session = AgentSession.from_settings(settings)

    with pytest.raises(InactiveAgentSession, match="not active"):
        _ = session.checkpoints


@pytest.mark.asyncio
async def test_session_respond_approval_rejects_non_waiting_run(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        base_url="https://example.test",
    )
    store = SqliteCheckpointStore(settings.database_path)
    run_id = "completed-run"
    seed_run(store, run_id, tmp_path, status=RunStatus.COMPLETED, final_message="done")

    async with AgentSession.from_settings(settings) as session:
        with pytest.raises(ResumeError, match="not waiting for tool approval"):
            await session.respond_approval(run_id, ApprovalVerdict(ApprovalDecision.APPROVE))


def test_make_sandbox_factory_returns_local_by_default() -> None:
    factory = make_sandbox_factory(ProjectConfig())

    assert factory is LocalSandbox


def test_make_sandbox_factory_returns_docker_when_configured() -> None:
    config = ProjectConfig(
        sandbox=SandboxConfig(kind="docker", image="python:3.12", workspace_mount="/mnt/ws")
    )

    factory = make_sandbox_factory(config)

    assert isinstance(factory, DockerSandboxFactory)
