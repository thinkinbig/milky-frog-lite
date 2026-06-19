from pathlib import Path

from milky_frog.skills import SkillCatalog


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
