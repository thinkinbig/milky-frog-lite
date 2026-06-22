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


class SkillCatalog:
    """Discovers user and project Skills, with project Skills taking precedence."""

    def __init__(self, user_directory: Path, project_directory: Path) -> None:
        self._paths = self._discover(user_directory)
        self._paths.update(self._discover(project_directory))

    def summaries(self) -> tuple[SkillSummary, ...]:
        return tuple(self._load(path).summary for _, path in sorted(self._paths.items()))

    def load(self, name: str) -> Skill:
        try:
            path = self._paths[name]
        except KeyError as error:
            raise KeyError(f"unknown skill: {name}") from error
        return self._load(path)

    def _discover(self, directory: Path) -> dict[str, Path]:
        if not directory.is_dir():
            return {}
        discovered: dict[str, Path] = {}
        for path in directory.glob("*/SKILL.md"):
            try:
                skill = self._load(path)
            except InvalidSkillError as exc:
                logger.warning("skipping malformed skill file %s: %s", path, exc)
                continue
            discovered[skill.summary.name] = path
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
