from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


class InvalidSkillError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SkillSummary:
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class Skill:
    summary: SkillSummary
    instructions: str
    path: Path


@dataclass(frozen=True, slots=True)
class _Discovered:
    """Cached metadata for a discovered SKILL.md; populated once at construction.

    The catalog serves ``summaries`` and ``prompt_locations`` from this cache
    (they run on every Run when the system prompt is built) and only loads
    the full ``Skill`` body when ``load(name)`` is called on demand.
    """

    summary: SkillSummary
    path: Path


_BUNDLED_DIR = Path(__file__).parent / "bundled"


class SkillCatalog:
    """Discovers bundled, user, and project Skills.

    Priority (highest wins): project > user > bundled.
    """

    def __init__(self, user_directory: Path, project_directory: Path) -> None:
        self._skills: dict[str, _Discovered] = {}
        self._skills.update(self._discover(_BUNDLED_DIR))
        self._skills.update(self._discover(user_directory))
        self._skills.update(self._discover(project_directory))

    def summaries(self) -> tuple[SkillSummary, ...]:
        return tuple(entry.summary for entry in self._sorted_entries())

    def prompt_locations(self) -> tuple[tuple[str, str, Path], ...]:
        """Skill metadata for system-prompt injection (name, description, path)."""
        return tuple(
            (entry.summary.name, entry.summary.description, entry.path)
            for entry in self._sorted_entries()
        )

    def load(self, name: str) -> Skill:
        try:
            entry = self._skills[name]
        except KeyError as error:
            raise KeyError(f"unknown skill: {name}") from error
        return self._load(entry.path)

    def _sorted_entries(self) -> tuple[_Discovered, ...]:
        return tuple(self._skills[name] for name in sorted(self._skills))

    def _discover(self, directory: Path) -> dict[str, _Discovered]:
        if not directory.is_dir():
            return {}
        discovered: dict[str, _Discovered] = {}
        for path in directory.glob("*/SKILL.md"):
            try:
                summary = self._load(path).summary
            except InvalidSkillError as exc:
                logger.warning("skipping malformed skill file %s: %s", path, exc)
                continue
            discovered[summary.name] = _Discovered(summary, path)
        return discovered

    @staticmethod
    def _load(path: Path) -> Skill:
        source = path.read_text(encoding="utf-8")
        if not source.startswith("---\n"):
            raise InvalidSkillError(f"missing YAML frontmatter: {path}")
        try:
            _, frontmatter, instructions = source.split("---", 2)
        except ValueError as error:
            raise InvalidSkillError(f"unterminated YAML frontmatter: {path}") from error
        metadata = yaml.safe_load(frontmatter)
        if not isinstance(metadata, dict):
            raise InvalidSkillError(f"invalid YAML frontmatter: {path}")
        name = metadata.get("name")
        description = metadata.get("description")
        if not isinstance(name, str) or not isinstance(description, str):
            raise InvalidSkillError(f"skill requires string name and description: {path}")
        return Skill(SkillSummary(name, description), instructions.strip(), path)
