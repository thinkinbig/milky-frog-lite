from __future__ import annotations

import shutil
from pathlib import Path

from milky_frog.harness.prompt import system_prompt
from milky_frog.harness.skills import SkillCatalog
from milky_frog.project import PROJECT_DIRNAME

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_SKILL = REPO_ROOT / "tests" / "fixtures" / "skills" / "tdd" / "SKILL.md"


def _install_fixture_skill(workspace: Path) -> Path:
    dest = workspace / PROJECT_DIRNAME / "skills" / "tdd" / "SKILL.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE_SKILL, dest)
    return dest


def test_tdd_skill_discovered_by_catalog(tmp_path: Path) -> None:
    skill_path = _install_fixture_skill(tmp_path)
    catalog = SkillCatalog(tmp_path / "missing-user-skills", tmp_path / PROJECT_DIRNAME / "skills")

    summaries = catalog.summaries()
    assert len(summaries) == 1
    assert summaries[0].name == "tdd"
    assert "test-first" in summaries[0].description

    loaded = catalog.load("tdd")
    assert loaded.path == skill_path
    assert "Vertical slices" in loaded.instructions


def test_tdd_skill_injected_into_system_prompt(tmp_path: Path) -> None:
    _install_fixture_skill(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    prompt = system_prompt(tmp_path, home=home)

    assert "<available_skills>" in prompt
    assert "<name>tdd</name>" in prompt
    assert "red-green-refactor" in prompt
    assert "Use read_file to load a skill's SKILL.md" in prompt
    assert f"{PROJECT_DIRNAME}/skills/tdd/SKILL.md" in prompt


def test_repo_tdd_skill_in_system_prompt_when_present() -> None:
    """Smoke test against the real workspace copy under .milky-frog/skills/."""
    skill = REPO_ROOT / PROJECT_DIRNAME / "skills" / "tdd" / "SKILL.md"
    if not skill.is_file():
        return

    prompt = system_prompt(REPO_ROOT, home=REPO_ROOT / "missing-home")

    assert "<name>tdd</name>" in prompt
    assert skill.resolve().as_posix() in prompt
