from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from milky_frog.adapters.docker import DockerSandboxFactory
from milky_frog.adapters.local import LocalSandbox
from milky_frog.app.session import (
    AgentSession,
    AgentSessionConfig,
    InactiveAgentSession,
    MissingModelConfiguration,
)
from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.core.runtime.assemble import make_sandbox_factory
from milky_frog.core.sandbox import CommandResult, Sandbox
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
    ToolResult,
)
from milky_frog.events import EventHub, Handler, RunCancelled, RunStarted
from milky_frog.harness.harness import AgentHarness
from milky_frog.harness.state import unmatched_tool_calls
from milky_frog.harness.tools import ToolContext
from milky_frog.harness.tools.builtins.fetch import FetchTool
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
async def test_session_loads_mcp_config_from_first_run_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Opening a Session must not bind project config to the launch directory."""
    launch_workspace = tmp_path / "launch"
    run_workspace = tmp_path / "run"
    launch_workspace.mkdir()
    (run_workspace / ".milky-frog").mkdir(parents=True)
    (launch_workspace / ".milky-frog").mkdir()
    (launch_workspace / ".milky-frog" / "mcp.json").write_text(
        '{"mcpServers": {"launch": {"command": "launch-server"}}}', encoding="utf-8"
    )
    (run_workspace / ".milky-frog" / "mcp.json").write_text(
        '{"mcpServers": {"run": {"command": "run-server"}}}', encoding="utf-8"
    )
    monkeypatch.chdir(launch_workspace)

    connected: list[set[str]] = []

    async def fake_connect_many(self: object, servers: dict[str, object]) -> None:
        del self
        connected.append(set(servers))

    async def fake_stream(self: OpenAIModel, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del self, request
        yield StreamDone(ModelResponse(content="done"))

    monkeypatch.setattr("milky_frog.app.session.McpClientManager.connect_many", fake_connect_many)
    monkeypatch.setattr(OpenAIModel, "stream", fake_stream)

    settings = _settings(tmp_path, base_url="https://example.test")
    async with AgentSession.from_settings(settings) as session:
        assert connected == []
        result = await session.start_new("build it", run_workspace)

    assert result.status is RunStatus.COMPLETED
    assert connected == [{"run"}]


@pytest.mark.asyncio
async def test_session_start_new_drops_unresolvable_skill_from_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A selected Skill that fails to load is recorded in neither injection nor metadata."""
    requests: list[ModelRequest] = []

    async def fake_stream(self: OpenAIModel, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del self
        requests.append(request)
        yield StreamDone(ModelResponse(content="done"))

    monkeypatch.setattr(OpenAIModel, "stream", fake_stream)
    settings = _settings(tmp_path, base_url="https://example.test")

    async with AgentSession.from_settings(settings) as session:
        # No skills catalog exists under this home, so the name cannot resolve.
        result = await session.start_new("build it", tmp_path, selected_skills=("does-not-exist",))

    assert result.status is RunStatus.COMPLETED
    state = SqliteCheckpointStore(settings.database_path).load_state(result.run_id)
    # The unresolved Skill must not leave a trace: no injected instructions and no
    # recorded name, so observability never claims a Skill that was not injected.
    assert state.run_extra == ()
    assert state.selected_skills == ()
    assert "does-not-exist" not in requests[0].messages[0].content


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


@pytest.mark.asyncio
async def test_session_subagent_tool_runs_nested_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The top-level Run can delegate to `subagent`, which runs an independent nested Run."""
    requests: list[ModelRequest] = []

    async def fake_stream(self: OpenAIModel, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del self
        requests.append(request)
        call_number = len(requests)
        if call_number == 1:
            yield StreamDone(
                ModelResponse(
                    tool_calls=(ToolCall("call-1", "subagent", {"prompt": "investigate X"}),)
                )
            )
            return
        if call_number == 2:
            yield StreamDone(ModelResponse(content="nested report"))
            return
        yield StreamDone(ModelResponse(content="top-level done"))

    monkeypatch.setattr(OpenAIModel, "stream", fake_stream)
    settings = _settings(tmp_path, base_url="https://example.test")

    async with AgentSession.from_settings(settings) as session:
        # subagent is gated (requires_approval=True); allow it at the top level
        # so this test exercises the nested Run rather than an approval halt.
        session.policy.allow("subagent")
        result = await session.start_new("delegate this", tmp_path)

    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "top-level done"

    store = SqliteCheckpointStore(settings.database_path)
    state = store.load_state(result.run_id)
    tool_msgs = [m.content for m in state.messages if m.role.value == "tool"]
    assert tool_msgs == ["nested report"]

    # The nested Run is its own independent, inspectable Checkpoint record.
    runs = store.list_runs()
    assert len(runs) == 2
    run_ids = {run.run_id for run in runs}
    assert result.run_id in run_ids
    nested_run_id = next(rid for rid in run_ids if rid != result.run_id)
    nested_state = store.load_state(nested_run_id)
    assert nested_state.messages[0].content == "investigate X"
    assert nested_state.run_kind == "subagent"
    assert nested_state.parent_run_id == result.run_id


@pytest.mark.asyncio
async def test_session_subagent_requires_approval_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """subagent is gated (requires_approval=True): delegation halts for approval
    instead of silently spawning a network-capable nested Run. Without this, a
    model could reach fetch/web_search egress with zero approval prompts."""
    requests: list[ModelRequest] = []

    async def fake_stream(self: OpenAIModel, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del self
        requests.append(request)
        yield StreamDone(
            ModelResponse(tool_calls=(ToolCall("call-1", "subagent", {"prompt": "investigate X"}),))
        )

    monkeypatch.setattr(OpenAIModel, "stream", fake_stream)
    settings = _settings(tmp_path, base_url="https://example.test")

    async with AgentSession.from_settings(settings) as session:
        result = await session.start_new("delegate this", tmp_path)

    assert result.status is RunStatus.WAITING_FOR_APPROVAL
    assert "subagent" in result.final_message
    # The nested Run never started — only the top-level Run exists.
    store = SqliteCheckpointStore(settings.database_path)
    assert len(store.list_runs()) == 1


@pytest.mark.asyncio
async def test_session_subagent_write_requires_docker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    requests: list[ModelRequest] = []

    async def fake_stream(self: OpenAIModel, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del self
        requests.append(request)
        if len(requests) == 1:
            yield StreamDone(
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            "call-1",
                            "subagent",
                            {"prompt": "implement X", "capability": "write"},
                        ),
                    )
                )
            )
            return
        tool_messages = [message for message in request.messages if message.role.value == "tool"]
        assert '[sandbox].kind = "docker"' in tool_messages[-1].content
        yield StreamDone(ModelResponse(content="write delegation rejected safely"))

    monkeypatch.setattr(OpenAIModel, "stream", fake_stream)
    settings = _settings(tmp_path, base_url="https://example.test")

    async with AgentSession.from_settings(settings) as session:
        session.policy.allow("subagent")
        result = await session.start_new("delegate a write", tmp_path)

    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "write delegation rejected safely"
    assert len(SqliteCheckpointStore(settings.database_path).list_runs()) == 1


@pytest.mark.asyncio
async def test_session_subagent_write_uses_isolated_worktree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_dir = tmp_path / ".milky-frog"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        '[sandbox]\nkind = "docker"\nimage = "python:3.12"\n',
        encoding="utf-8",
    )
    local = LocalSandbox(tmp_path)
    initialized = await local.run_command(
        "git init && git add .milky-frog/config.toml && "
        "git -c user.name=test -c user.email=test@example.com commit -m init",
        timeout_seconds=10,
    )
    assert isinstance(initialized, CommandResult)
    assert initialized.exit_code == 0, initialized.output

    class RecordingFactory:
        def __init__(self) -> None:
            self.calls: list[Path] = []

        def __call__(self, workspace: Path) -> Sandbox:
            self.calls.append(workspace)
            return LocalSandbox(workspace)

    factory = RecordingFactory()
    requests: list[ModelRequest] = []

    async def fake_stream(self: OpenAIModel, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del self
        requests.append(request)
        match len(requests):
            case 1:
                yield StreamDone(
                    ModelResponse(
                        tool_calls=(
                            ToolCall(
                                "call-1",
                                "subagent",
                                {"prompt": "implement X", "capability": "write"},
                            ),
                        )
                    )
                )
            case 2:
                yield StreamDone(
                    ModelResponse(
                        tool_calls=(
                            ToolCall(
                                "call-2",
                                "write_file",
                                {"path": "feature.txt", "content": "isolated"},
                            ),
                        )
                    )
                )
            case 3:
                yield StreamDone(ModelResponse(content="implemented"))
            case _:
                raise AssertionError("model should not be called again before merge approval")

    monkeypatch.setattr(OpenAIModel, "stream", fake_stream)
    settings = _settings(tmp_path, base_url="https://example.test")
    session_config = AgentSessionConfig(sandbox_factory=factory)

    async with AgentSession.from_settings(settings, config=session_config) as session:
        session.policy.allow("subagent")
        result = await session.start_new("delegate a write", tmp_path)

    # A dirty worktree deterministically pauses the Run for a merge decision
    # (see AgentLoop.advance / MergeWorktreeTool) instead of letting the model
    # finish without anyone reviewing it.
    assert result.status is RunStatus.WAITING_FOR_APPROVAL
    state = SqliteCheckpointStore(settings.database_path).load_state(result.run_id)
    subagent_result = next(
        message.content for message in state.messages if "worktree=" in message.content
    )
    worktree_text = subagent_result.split("worktree=", 1)[1].split(", branch=", 1)[0]
    worktree = Path(worktree_text)
    assert (worktree / "feature.txt").read_text(encoding="utf-8") == "isolated"
    assert not (tmp_path / "feature.txt").exists()

    pending = unmatched_tool_calls(state.messages)
    assert [call.name for call in pending] == ["merge_worktree"]

    cleanup = await local.run_command(
        f"git worktree remove --force {worktree}",
        timeout_seconds=10,
    )
    assert isinstance(cleanup, CommandResult)
    assert cleanup.exit_code == 0, cleanup.output


@pytest.mark.asyncio
async def test_session_subagent_fetch_runs_without_approval_pause(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`fetch`/`web_search` require approval by default, but a nested subagent
    Run has no way for anyone to ever resolve that pause — the nested policy
    must auto-approve read-only Tools instead of deadlocking on them."""
    requests: list[ModelRequest] = []

    async def fake_stream(self: OpenAIModel, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del self
        requests.append(request)
        call_number = len(requests)
        if call_number == 1:
            yield StreamDone(
                ModelResponse(tool_calls=(ToolCall("call-1", "subagent", {"prompt": "fetch it"}),))
            )
            return
        if call_number == 2:
            yield StreamDone(
                ModelResponse(
                    tool_calls=(ToolCall("call-2", "fetch", {"url": "https://example.test"}),)
                )
            )
            return
        if call_number == 3:
            yield StreamDone(ModelResponse(content="nested done"))
            return
        yield StreamDone(ModelResponse(content="top-level done"))

    async def fake_fetch_execute(
        self: FetchTool, context: ToolContext, input: object
    ) -> ToolResult:
        del self, context, input
        return ToolResult("mocked fetch content")

    monkeypatch.setattr(OpenAIModel, "stream", fake_stream)
    monkeypatch.setattr(FetchTool, "execute", fake_fetch_execute)
    settings = _settings(tmp_path, base_url="https://example.test")

    async with AgentSession.from_settings(settings) as session:
        # Approve delegation at the boundary; the point of this test is that the
        # nested fetch does NOT itself pause once inside the subagent.
        session.policy.allow("subagent")
        result = await session.start_new("delegate this", tmp_path)

    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "top-level done"

    store = SqliteCheckpointStore(settings.database_path)
    state = store.load_state(result.run_id)
    tool_msgs = [m.content for m in state.messages if m.role.value == "tool"]
    # If fetch had paused for approval instead, this would be the
    # "approval needed for: fetch" message and the Run would never reach
    # "nested done" (there is no caller who could ever approve it).
    assert tool_msgs == ["nested done"]


@pytest.mark.asyncio
async def test_session_subagent_shares_hub_but_tui_ignores_nested_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The nested subagent Run intentionally shares the session's one
    EventHub (CheckpointHandler/LangfuseHandler key everything off run_id, so
    a second Run on the same hub is safe for them, and it's how the nested
    Run gets its own Checkpoint record). A run_id-naive Handler (a plain
    RunStarted spy here, standing in for TuiPresentationHandler's own
    filtering, tested directly in tests/tui/test_tui_renderer.py) does see
    both Runs on the shared hub — that's expected, not a leak."""
    requests: list[ModelRequest] = []

    async def fake_stream(self: OpenAIModel, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del self
        requests.append(request)
        call_number = len(requests)
        if call_number == 1:
            yield StreamDone(
                ModelResponse(
                    tool_calls=(ToolCall("call-1", "subagent", {"prompt": "investigate X"}),)
                )
            )
            return
        if call_number == 2:
            yield StreamDone(ModelResponse(content="nested report"))
            return
        yield StreamDone(ModelResponse(content="top-level done"))

    monkeypatch.setattr(OpenAIModel, "stream", fake_stream)
    settings = _settings(tmp_path, base_url="https://example.test")

    seen_run_ids: list[str] = []

    class SpyHandler(Handler):
        def register(self, hub: EventHub) -> None:
            hub.on(RunStarted)(self._record)

        async def _record(self, event: RunStarted, deps: object = None) -> None:
            seen_run_ids.append(event.run_id)

    async with AgentSession.from_settings(settings, bundles=[SpyHandler()]) as session:
        session.policy.allow("subagent")  # gated by default; approve at the boundary
        result = await session.start_new("delegate this", tmp_path)

    # Both the top-level Run and the nested subagent Run broadcast RunStarted
    # on the same shared hub.
    assert result.run_id in seen_run_ids
    assert len(seen_run_ids) == 2


def test_make_sandbox_factory_returns_local_by_default() -> None:
    factory = make_sandbox_factory(ProjectConfig())

    assert factory is LocalSandbox


def test_make_sandbox_factory_returns_docker_when_configured() -> None:
    config = ProjectConfig(
        sandbox=SandboxConfig(kind="docker", image="python:3.12", workspace_mount="/mnt/ws")
    )

    factory = make_sandbox_factory(config)

    assert isinstance(factory, DockerSandboxFactory)
