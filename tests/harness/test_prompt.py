from __future__ import annotations

from pathlib import Path

from milky_frog.harness.prompt import (
    BuildSystemPromptOptions,
    agent_context_section,
    build_system_prompt,
    format_agent_context,
    system_prompt,
)
from milky_frog.harness.prompt_context import AgentContext, ContextFile, load_agent_context
from milky_frog.harness.skills import SkillCatalog
from milky_frog.project import PROJECT_DIRNAME


def _write_skill(directory: Path, name: str, description: str, instructions: str) -> None:
    path = directory / name
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{instructions}\n",
        encoding="utf-8",
    )


def test_system_prompt_includes_base_identity(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    prompt = system_prompt(workspace)

    assert "Milky Frog" in prompt
    assert "<rules>" in prompt
    assert "prefer narrow commands" in prompt.lower()
    assert f"Current working directory: {workspace.resolve().as_posix()}" in prompt


def test_system_prompt_injects_workspace_agents_md(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("Always run pytest before committing.\n", encoding="utf-8")

    prompt = build_system_prompt(
        BuildSystemPromptOptions(
            workspace=workspace,
            agent_context=load_agent_context(workspace, home),
        )
    )

    assert "<project_context>" in prompt
    assert "Always run pytest before committing." in prompt
    assert f'path="{workspace.resolve().as_posix()}/AGENTS.md"' in prompt


def test_load_context_files_orders_global_then_ancestors(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "AGENTS.md").write_text("global rules\n", encoding="utf-8")

    root = tmp_path / "root"
    company = root / "company"
    project = company / "app"
    project.mkdir(parents=True)
    (root / "AGENTS.md").write_text("root rules\n", encoding="utf-8")
    (company / "AGENTS.md").write_text("company rules\n", encoding="utf-8")
    (project / "CLAUDE.md").write_text("project rules\n", encoding="utf-8")

    files = load_agent_context(project, home).context_files

    assert [file.content.strip() for file in files] == [
        "global rules",
        "root rules",
        "company rules",
        "project rules",
    ]


def test_system_prompt_injects_skills_and_append_rules(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project_skills = tmp_path / "workspace" / PROJECT_DIRNAME / "skills"
    _write_skill(home / "skills", "review", "Review code carefully", "review steps")
    _write_skill(project_skills, "tdd", "Write tests first", "tdd steps")
    (home / "APPEND_SYSTEM.md").write_text("Prefer small diffs.\n", encoding="utf-8")

    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)

    prompt = build_system_prompt(
        BuildSystemPromptOptions(
            workspace=workspace,
            agent_context=load_agent_context(workspace, home),
        )
    )

    assert "Prefer small diffs." in prompt
    assert "<available_skills>" in prompt
    assert "<name>review</name>" in prompt
    assert "<name>tdd</name>" in prompt
    assert "Use read_file to load a skill's SKILL.md" in prompt


def test_build_system_prompt_is_pure_assembly(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = ContextFile(workspace / "AGENTS.md", "project-only")

    prompt = build_system_prompt(
        BuildSystemPromptOptions(
            workspace=workspace,
            agent_context=AgentContext(
                append_system="extra rule",
                context_files=(context,),
                skill_locations=(("demo", "Demo skill", workspace / "skill" / "SKILL.md"),),
            ),
        )
    )

    assert "extra rule" in prompt
    assert "project-only" in prompt
    assert "<name>demo</name>" in prompt


def test_handler_injection_matches_direct_build(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("Run pytest first.\n", encoding="utf-8")
    (home / "APPEND_SYSTEM.md").write_text("Keep diffs small.\n", encoding="utf-8")

    context = load_agent_context(workspace, home)
    injected = format_agent_context(context)
    assert injected is not None

    via_handler = system_prompt(workspace, extra_sections=(injected,))
    via_build = build_system_prompt(
        BuildSystemPromptOptions(workspace=workspace, agent_context=context)
    )

    assert via_handler == via_build


def test_agent_context_section_matches_format_agent_context(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("Project rules.\n", encoding="utf-8")

    context = load_agent_context(workspace, home)
    assert agent_context_section(workspace, home) == format_agent_context(context)


def test_skill_catalog_prompt_locations(tmp_path: Path) -> None:
    user = tmp_path / "user"
    project = tmp_path / "project"
    _write_skill(user, "review", "user description", "user instructions")
    _write_skill(project, "review", "project description", "project instructions")

    catalog = SkillCatalog(user, project)

    assert catalog.prompt_locations() == (
        ("review", "project description", project / "review" / "SKILL.md"),
    )
