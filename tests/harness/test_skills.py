from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.domain import (
    MessageRole,
    ModelChunk,
    ModelRequest,
    ModelResponse,
    ResumeError,
    RunRequest,
    RunStatus,
    StreamDone,
)
from milky_frog.events import EventHub
from milky_frog.events.events import RunBeforeResume
from milky_frog.harness.context import ContextManager
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

    # Bundled skills are always discovered, so ``summaries()[0]`` is no longer
    # guaranteed to be ``review``. Look up by name to assert the override.
    assert catalog.load("review").instructions == "project instructions"
    descriptions = {name: description for name, description, _path in catalog.prompt_locations()}
    assert descriptions["review"] == "project description"


def test_summaries_and_prompt_locations_serve_cached_metadata(tmp_path: Path) -> None:
    """Metadata accessors must reflect state at construction time.

    ``summaries()`` and ``prompt_locations()`` are called on every Run to build
    the system prompt — they cannot afford to re-read every SKILL.md on disk.
    The contract is: cache the summary at discovery, only ``load(name)`` reads
    fresh on demand. We verify by mutating a file after construction and
    asserting metadata stays frozen while ``load`` sees the change.
    """
    user = tmp_path / "user"
    project = tmp_path / "project"
    skill_file = user / "review" / "SKILL.md"
    _write_skill(user, "review", "original description", "original instructions")

    catalog = SkillCatalog(user, project)
    assert catalog.load("review").summary.description == "original description"

    # Mutate the file on disk after the catalog is built.
    skill_file.write_text(
        "---\nname: review\ndescription: MUTATED\n---\nnew instructions\n",
        encoding="utf-8",
    )

    # Cached accessors must still report the construction-time description.
    assert {s.description for s in catalog.summaries() if s.name == "review"} == {
        "original description",
    }
    assert dict((n, d) for n, d, _ in catalog.prompt_locations())["review"] == (
        "original description"
    )

    # load(name) is the one path that must read fresh — on-demand contract.
    assert catalog.load("review").summary.description == "MUTATED"
    assert catalog.load("review").instructions == "new instructions"


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
    """When the loader has nothing to report it returns ``None``.

    Bundled skills are always discoverable, so an empty workspace + empty home
    is no longer enough to make the loader return ``None``. The only reachable
    None path is now: bundled cache cleared *and* no user/project content.
    Monkey-patch ``load_agent_context`` to simulate that case.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    home.mkdir()

    from milky_frog.harness import prompt

    loader = make_context_loader(home)
    original = prompt.load_agent_context

    def empty(workspace: Path, home: Path) -> object:  # type: ignore[no-untyped-def]
        from milky_frog.harness.prompt_context import AgentContext

        del workspace, home
        return AgentContext()

    prompt.load_agent_context = empty
    try:
        assert loader(workspace) is None
    finally:
        prompt.load_agent_context = original


def test_context_loader_includes_bundled_skill_metadata(tmp_path: Path) -> None:
    """Bundled skills always contribute to the context section, even with an
    empty user home and empty workspace. The model can discover them by name."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    home.mkdir()

    loader = make_context_loader(home)
    section = loader(workspace)

    assert section is not None
    assert "<available_skills>" in section
    # A known bundled skill must be present.
    from milky_frog.harness.skills.catalog import _BUNDLED_DIR

    bundled_names = {path.parent.name for path in _BUNDLED_DIR.glob("*/SKILL.md")}
    assert bundled_names
    for name in bundled_names:
        assert f"<name>{name}</name>" in section


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
    """Skill metadata injected via ContextLoader must be visible in the system
    message ContextManager rebuilds — the one the model actually sees. The system
    prompt is no longer persisted in the transcript, so assemble it here."""
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
    system_message = ContextManager(make_context_loader(home)).assemble(state)[0]
    assert system_message.role is MessageRole.SYSTEM
    assert "<name>style</name>" in system_message.content
    assert "Style guide" in system_message.content


# ── Resume: skill injection must survive checkpoint reload ────────────────


@pytest.mark.asyncio
async def test_run_extra_survives_checkpoint_reload(tmp_path: Path) -> None:
    """``run_extra`` (eager skill injection) must round-trip through the snapshot,
    otherwise resume / continue_with lose the activated-skill prompt and the
    model no longer sees instructions that say 'apply throughout the task'."""

    skill_text = '<active_skill name="review">\nAlways check edge cases.\n</active_skill>'

    class RecordingModel:
        """Records every system prompt the loop sees, then completes."""

        def __init__(self) -> None:
            self.system_prompts: list[str] = []

        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            system = next((m for m in request.messages if m.role is MessageRole.SYSTEM), None)
            assert system is not None
            self.system_prompts.append(system.content)
            yield StreamDone(ModelResponse(content="done"))

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SqliteCheckpointStore(tmp_path / "state.db")
    model = RecordingModel()
    harness = make_harness(model, ToolRegistry(), store, EventHub())

    first = await harness.run(
        RunRequest("hello", workspace, skill_content=skill_text, selected_skills=("review",)),
    )
    assert first.status is RunStatus.COMPLETED
    assert skill_text in model.system_prompts[-1]

    # Snapshot must persist the activated skill instructions (ADR-0014).
    state_before = store.load_state(first.run_id)
    assert state_before.run_extra == (skill_text,)
    assert state_before.selected_skills == ("review",)

    # Resume fires another model call — the skill prompt must still be there.
    continued = await harness.resume(first.run_id, max_model_calls=5, prompt="again")
    assert continued.status is RunStatus.COMPLETED
    state_after = store.load_state(first.run_id)
    assert state_after.run_extra == (skill_text,)
    assert state_after.selected_skills == ("review",)
    assert skill_text in model.system_prompts[-1]  # last model call after resume


@pytest.mark.asyncio
async def test_run_extra_drops_when_no_skill_injected(tmp_path: Path) -> None:
    """Runs without ``skill_content`` must not gain a phantom prompt on resume
    — backstop that the persisted field defaults to empty, not stale state."""

    class NoOpModel:
        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            yield StreamDone(ModelResponse(content="done"))

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SqliteCheckpointStore(tmp_path / "state.db")
    harness = make_harness(NoOpModel(), ToolRegistry(), store, EventHub())

    first = await harness.run(RunRequest("hello", workspace))
    assert first.status is RunStatus.COMPLETED

    await harness.resume(first.run_id, max_model_calls=5, prompt="again")
    loaded = store.load_state(first.run_id)
    assert loaded.run_extra == ()


@pytest.mark.asyncio
async def test_resume_run_extra_re_applies_and_clears(tmp_path: Path) -> None:
    """Passing ``run_extra`` to resume overrides the persisted value, so mid-run
    skill activation and ``/skill off`` both take effect; ``None`` preserves it."""

    activated = '<active_skill name="review">\nCheck edge cases.\n</active_skill>'

    class RecordingModel:
        def __init__(self) -> None:
            self.system_prompts: list[str] = []

        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            system = next((m for m in request.messages if m.role is MessageRole.SYSTEM), None)
            assert system is not None
            self.system_prompts.append(system.content)
            yield StreamDone(ModelResponse(content="done"))

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SqliteCheckpointStore(tmp_path / "state.db")
    model = RecordingModel()
    harness = make_harness(model, ToolRegistry(), store, EventHub())

    # Start with no skills.
    first = await harness.run(RunRequest("hello", workspace))
    assert first.status is RunStatus.COMPLETED
    assert activated not in model.system_prompts[-1]

    # Activate a skill mid-run: resume with an explicit run_extra.
    await harness.resume(
        first.run_id,
        max_model_calls=5,
        prompt="again",
        run_extra=(activated,),
        selected_skills=("review",),
    )
    assert store.load_state(first.run_id).run_extra == (activated,)
    assert store.load_state(first.run_id).selected_skills == ("review",)
    assert activated in model.system_prompts[-1]

    # A subsequent resume without run_extra preserves it (skills survive resume).
    await harness.resume(first.run_id, max_model_calls=5, prompt="more")
    assert store.load_state(first.run_id).run_extra == (activated,)
    assert activated in model.system_prompts[-1]

    # Deactivate mid-run: resume with an empty run_extra clears it.
    await harness.resume(
        first.run_id,
        max_model_calls=5,
        prompt="done",
        run_extra=(),
        selected_skills=(),
    )
    assert store.load_state(first.run_id).run_extra == ()
    assert store.load_state(first.run_id).selected_skills == ()
    assert activated not in model.system_prompts[-1]


@pytest.mark.asyncio
async def test_resume_emits_changed_selected_skills(tmp_path: Path) -> None:
    """A changed Skill selection reaches observability before the next model call."""

    class NoOpModel:
        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            del request
            yield StreamDone(ModelResponse(content="done"))

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SqliteCheckpointStore(tmp_path / "state.db")
    hub = EventHub()
    seen: list[RunBeforeResume] = []

    @hub.on(RunBeforeResume)
    async def record(event: RunBeforeResume, _deps: object = None) -> None:
        seen.append(event)

    harness = make_harness(NoOpModel(), ToolRegistry(), store, hub)
    first = await harness.run(RunRequest("hello", workspace))

    await harness.resume(
        first.run_id,
        max_model_calls=5,
        prompt="again",
        run_extra=('<active_skill name="review" />',),
        selected_skills=("review",),
    )

    assert seen[-1].selected_skills == ("review",)


@pytest.mark.asyncio
async def test_resume_rejects_unpaired_skill_metadata(tmp_path: Path) -> None:
    class NoOpModel:
        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
            del request
            yield StreamDone(ModelResponse(content="done"))

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SqliteCheckpointStore(tmp_path / "state.db")
    harness = make_harness(NoOpModel(), ToolRegistry(), store, EventHub())
    first = await harness.run(RunRequest("hello", workspace))

    with pytest.raises(ResumeError, match="run_extra and selected_skills"):
        await harness.resume(first.run_id, max_model_calls=5, selected_skills=("review",))
