from __future__ import annotations

import shutil
from pathlib import Path

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

    # Bundled skills are always present — find the test fixture by name.
    summaries = {summary.name: summary for summary in catalog.summaries()}
    assert "tdd" in summaries
    assert "test-first" in summaries["tdd"].description

    loaded = catalog.load("tdd")
    assert loaded.path == skill_path
    assert "Vertical slices" in loaded.instructions
