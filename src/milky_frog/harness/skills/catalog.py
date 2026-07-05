from __future__ import annotations

import logging
from collections.abc import Iterable
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
class SkillRecord:
    """L1 metadata + path for one SKILL.md; immutable, cheap to hold.

    The single per-skill value the catalog indexes. ``summary`` is the L1
    metadata injected into ``<available_skills>`` on every system-prompt
    rebuild; ``path`` is where ``load()`` reads the L2 body from on demand.
    """

    summary: SkillSummary
    path: Path


@dataclass(frozen=True, slots=True)
class Skill:
    summary: SkillSummary
    instructions: str
    path: Path


_BUNDLED_DIR = Path(__file__).parent / "bundled"


def discover(directory: Path) -> tuple[Path, ...]:
    """Glob one directory for ``*/SKILL.md``; no file reads.

    Returns an empty tuple when ``directory`` is missing or not a directory.
    Discovery is pure filesystem lookup — parsing happens in ``index``.
    """
    if not directory.is_dir():
        return ()
    return tuple(directory.glob("*/SKILL.md"))


def index(paths: Iterable[Path]) -> SkillIndex:
    """Parse frontmatter for each path into a frozen metadata index.

    ``paths`` is consumed lowest-priority first; a later path with the same
    skill name overrides an earlier one (project > user > bundled). Files with
    malformed frontmatter are logged and skipped, not raised.
    """
    records: dict[str, SkillRecord] = {}
    for path in paths:
        try:
            summary = _parse_frontmatter(path)
        except InvalidSkillError as exc:
            logger.warning("skipping malformed skill file %s: %s", path, exc)
            continue
        records[summary.name] = SkillRecord(summary, path)
    return SkillIndex(records)


@dataclass(frozen=True, slots=True)
class SkillIndex:
    """Frozen metadata index built once at catalog construction.

    ``summaries()`` and ``prompt_locations()`` are pure functions of the frozen
    records — they never touch disk. ``load(name)`` reads the instructions body
    fresh on demand.
    """

    records: dict[str, SkillRecord]

    def summaries(self) -> tuple[SkillSummary, ...]:
        return tuple(record.summary for record in self._sorted())

    def prompt_locations(self) -> tuple[tuple[str, str, Path], ...]:
        """Skill metadata for system-prompt injection (name, description, path)."""
        return tuple(
            (record.summary.name, record.summary.description, record.path)
            for record in self._sorted()
        )

    def load(self, name: str) -> Skill:
        try:
            record = self.records[name]
        except KeyError as error:
            raise KeyError(f"unknown skill: {name}") from error
        return _read_skill(record.path)

    def _sorted(self) -> tuple[SkillRecord, ...]:
        return tuple(self.records[name] for name in sorted(self.records))


class SkillCatalog:
    """Discovers bundled, user, and project Skills.

    Priority (highest wins): project > user > bundled.

    A thin facade over ``discover`` (glob) and ``index`` (parse frontmatter):
    construction merges the three sources by priority and freezes the result
    into a ``SkillIndex``; the public accessors delegate to it.
    """

    def __init__(self, user_directory: Path, project_directory: Path) -> None:
        paths = (
            *discover(_BUNDLED_DIR),
            *discover(user_directory),
            *discover(project_directory),
        )
        self._index = index(paths)

    def summaries(self) -> tuple[SkillSummary, ...]:
        return self._index.summaries()

    def prompt_locations(self) -> tuple[tuple[str, str, Path], ...]:
        return self._index.prompt_locations()

    def load(self, name: str) -> Skill:
        return self._index.load(name)


def _split_frontmatter(path: Path) -> tuple[dict[str, object], str]:
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
    return metadata, instructions


def _summary(metadata: dict[str, object], path: Path) -> SkillSummary:
    name = metadata.get("name")
    description = metadata.get("description")
    if not isinstance(name, str) or not isinstance(description, str):
        raise InvalidSkillError(f"skill requires string name and description: {path}")
    return SkillSummary(name, description)


def _parse_frontmatter(path: Path) -> SkillSummary:
    """L1: read frontmatter only, discard the instructions body."""
    metadata, _ = _split_frontmatter(path)
    return _summary(metadata, path)


def _read_skill(path: Path) -> Skill:
    """L2: read the full SKILL.md, including the instructions body."""
    metadata, instructions = _split_frontmatter(path)
    return Skill(_summary(metadata, path), instructions.strip(), path)
