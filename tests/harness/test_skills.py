from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.domain import ModelChunk, ModelRequest, ModelResponse, RunRequest, StreamDone
from milky_frog.events import EventHub, RunBeforeStart
from milky_frog.handlers.context import SystemPromptSection
from milky_frog.handlers.skills import AgentContextHandler
from milky_frog.harness.skills import SkillCatalog
from milky_frog.harness.tools import ToolRegistry
from tests.stubs import make_harness


def _write_skill(directory: Path, name: str, description: str, instructions: str) -> None:
    path = directory / name
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{instructions}\n",
        encoding="utf-8",
    )


def test_project_skill_overrides_user_skill(tmp_path: Path) -> None:
    user = tmp_path / "user"
    project = tmp_path / "project"
    _write_skill(user, "review", "user description", "user instructions")
    _write_skill(project, "review", "project description", "project instructions")

    catalog = SkillCatalog(user, project)

    assert catalog.summaries()[0].description == "project description"
    assert catalog.load("review").instructions == "project instructions"


# ── AgentContextHandler ────────────────────────────────────────────────────


def _make_event(workspace: Path) -> RunBeforeStart:
    return RunBeforeStart(
        run_id="run-1",
        request=RunRequest("hello", workspace),
        workspace=workspace,
    )


@pytest.mark.asyncio
async def test_handler_injects_skill_catalog_metadata(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    _write_skill(home / "skills", "review", "Code review skill", "Always check for typos.")

    bus = EventHub()
    AgentContextHandler(home).register(bus)

    results = await bus.broadcast(_make_event(workspace))
    injected = [r for r in results if isinstance(r, SystemPromptSection)]

    assert len(injected) == 1
    assert "<name>review</name>" in injected[0].content
    assert "Code review skill" in injected[0].content
    assert "Always check for typos." not in injected[0].content


@pytest.mark.asyncio
async def test_handler_returns_none_when_no_context(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    home.mkdir()

    bus = EventHub()
    AgentContextHandler(home).register(bus)

    results = await bus.broadcast(_make_event(workspace))
    injected = [r for r in results if isinstance(r, SystemPromptSection)]

    assert injected == []


@pytest.mark.asyncio
async def test_project_skills_override_user_skills_in_handler(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    project_skills = workspace / ".milky-frog" / "skills"
    workspace.mkdir()
    home = tmp_path / "home"
    _write_skill(home / "skills", "review", "user", "user instructions")
    _write_skill(project_skills, "review", "project", "project instructions")

    bus = EventHub()
    AgentContextHandler(home).register(bus)

    results = await bus.broadcast(_make_event(workspace))
    injected = [r for r in results if isinstance(r, SystemPromptSection)]

    assert len(injected) == 1
    assert "<description>project</description>" in injected[0].content
    assert "<description>user</description>" not in injected[0].content


@pytest.mark.asyncio
async def test_handler_skips_malformed_skill(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    _write_skill(home / "skills", "good", "Good skill", "Good instructions.")
    bad = home / "skills" / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("no frontmatter here", encoding="utf-8")

    bus = EventHub()
    AgentContextHandler(home).register(bus)

    results = await bus.broadcast(_make_event(workspace))
    injected = [r for r in results if isinstance(r, SystemPromptSection)]

    assert len(injected) == 1
    assert "<name>good</name>" in injected[0].content
    assert "<name>bad</name>" not in injected[0].content


# ── Integration: skill catalog reaches the system message ─────────────────


@pytest.mark.asyncio
async def test_skill_catalog_appears_in_system_message(tmp_path: Path) -> None:
    """Skill metadata injected via RunBeforeStart must be visible in the first
    system message of the persisted transcript — the one the model actually sees."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    project_skills = workspace / ".milky-frog" / "skills"
    _write_skill(project_skills, "style", "Style guide", "Always use Oxford commas.")

    class ImmediateModel:
        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            yield StreamDone(ModelResponse(content="done"))

    store = SqliteCheckpointStore(tmp_path / "state.db")
    bus = EventHub()
    AgentContextHandler(home).register(bus)

    harness = make_harness(
        model=ImmediateModel(),
        tools=ToolRegistry(),
        checkpoints=store,
        hub=bus,
    )
    result = await harness.run(RunRequest("hello", workspace))

    state = store.load_state(result.run_id)
    system_message = state.messages[0]
    assert "<name>style</name>" in system_message.content
    assert "Style guide" in system_message.content
