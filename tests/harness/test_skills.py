from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.domain import ModelChunk, ModelRequest, ModelResponse, RunRequest, StreamDone
from milky_frog.handlers import LifecycleBus, RunBeforeStart, SystemPromptSection
from milky_frog.handlers.skills import SkillCatalogHandler
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


# ── SkillCatalogHandler ────────────────────────────────────────────────────


def _make_event(workspace: Path) -> RunBeforeStart:
    return RunBeforeStart(
        run_id="run-1",
        request=RunRequest("hello", workspace),
        workspace=workspace,
    )


@pytest.mark.asyncio
async def test_handler_injects_skill_into_system_prompt(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    user_skills = tmp_path / "user_skills"
    _write_skill(user_skills, "review", "Code review skill", "Always check for typos.")

    bus = LifecycleBus()
    SkillCatalogHandler(user_skills).register(bus)

    results = await bus.notify(_make_event(workspace))
    injected = [r for r in results if isinstance(r, SystemPromptSection)]

    assert len(injected) == 1
    assert "review" in injected[0].content
    assert "Always check for typos." in injected[0].content


@pytest.mark.asyncio
async def test_handler_returns_none_when_no_skills(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    user_skills = tmp_path / "user_skills"

    bus = LifecycleBus()
    SkillCatalogHandler(user_skills).register(bus)

    results = await bus.notify(_make_event(workspace))
    injected = [r for r in results if isinstance(r, SystemPromptSection)]

    assert injected == []


@pytest.mark.asyncio
async def test_project_skills_override_user_skills_in_handler(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    project_skills = workspace / ".milky-frog" / "skills"
    workspace.mkdir()
    user_skills = tmp_path / "user_skills"
    _write_skill(user_skills, "review", "user", "user instructions")
    _write_skill(project_skills, "review", "project", "project instructions")

    bus = LifecycleBus()
    SkillCatalogHandler(user_skills).register(bus)

    results = await bus.notify(_make_event(workspace))
    injected = [r for r in results if isinstance(r, SystemPromptSection)]

    assert len(injected) == 1
    assert "project instructions" in injected[0].content
    assert "user instructions" not in injected[0].content


@pytest.mark.asyncio
async def test_handler_skips_malformed_skill(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    user_skills = tmp_path / "user_skills"
    # Write a valid skill
    _write_skill(user_skills, "good", "Good skill", "Good instructions.")
    # Write a malformed skill (no frontmatter)
    bad = user_skills / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("no frontmatter here", encoding="utf-8")

    bus = LifecycleBus()
    SkillCatalogHandler(user_skills).register(bus)

    results = await bus.notify(_make_event(workspace))
    injected = [r for r in results if isinstance(r, SystemPromptSection)]

    assert len(injected) == 1
    assert "Good instructions." in injected[0].content


# ── Integration: skill content reaches the system message ─────────────────


@pytest.mark.asyncio
async def test_skill_instructions_appear_in_system_message(tmp_path: Path) -> None:
    """Skill content injected via RunBeforeStart must be visible in the first
    system message of the persisted transcript — the one the model actually sees."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project_skills = workspace / ".milky-frog" / "skills"
    _write_skill(project_skills, "style", "Style guide", "Always use Oxford commas.")

    class ImmediateModel:
        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            yield StreamDone(ModelResponse(content="done"))

    store = SqliteCheckpointStore(tmp_path / "state.db")
    bus = LifecycleBus()
    SkillCatalogHandler(tmp_path / "no_user_skills").register(bus)

    harness = make_harness(
        model=ImmediateModel(),
        tools=ToolRegistry(),
        checkpoints=store,
        handlers=bus,
    )
    result = await harness.run(RunRequest("hello", workspace))

    state = store.load_state(result.run_id)
    system_message = state.messages[0]
    assert "Always use Oxford commas." in system_message.content
