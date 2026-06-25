from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.domain import ModelChunk, ModelRequest, ModelResponse, RunRequest, StreamDone
from milky_frog.harness.prompt import make_context_loader
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


# ── ContextLoader ──────────────────────────────────────────────────────────


def test_context_loader_injects_skill_catalog_metadata(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    _write_skill(home / "skills", "review", "Code review skill", "Always check for typos.")

    loader = make_context_loader(home)
    section = loader(workspace)

    assert section is not None
    assert "<name>review</name>" in section
    assert "Code review skill" in section
    assert "Always check for typos." not in section  # metadata only, not instructions


def test_context_loader_returns_none_when_no_context(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    home.mkdir()

    loader = make_context_loader(home)
    assert loader(workspace) is None


def test_context_loader_project_skills_override_user_skills(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    project_skills = workspace / ".milky-frog" / "skills"
    workspace.mkdir()
    home = tmp_path / "home"
    _write_skill(home / "skills", "review", "user", "user instructions")
    _write_skill(project_skills, "review", "project", "project instructions")

    loader = make_context_loader(home)
    section = loader(workspace)

    assert section is not None
    assert "<description>project</description>" in section
    assert "<description>user</description>" not in section


def test_context_loader_skips_malformed_skill(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    _write_skill(home / "skills", "good", "Good skill", "Good instructions.")
    bad = home / "skills" / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("no frontmatter here", encoding="utf-8")

    loader = make_context_loader(home)
    section = loader(workspace)

    assert section is not None
    assert "<name>good</name>" in section
    assert "<name>bad</name>" not in section


# ── Integration: skill catalog reaches the system message ─────────────────


@pytest.mark.asyncio
async def test_skill_catalog_appears_in_system_message(tmp_path: Path) -> None:
    """Skill metadata injected via ContextLoader must be visible in the first
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
    harness = make_harness(
        model=ImmediateModel(),
        tools=ToolRegistry(),
        checkpoints=store,
        context_loader=make_context_loader(home),
    )
    result = await harness.run(RunRequest("hello", workspace))

    state = store.load_state(result.run_id)
    system_message = state.messages[0]
    assert "<name>style</name>" in system_message.content
    assert "Style guide" in system_message.content
