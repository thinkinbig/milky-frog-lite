from __future__ import annotations

from pathlib import Path

from milky_frog.handlers.bus import BaseHandler, LifecycleBus
from milky_frog.handlers.context import HandlerContext, SystemPromptSection
from milky_frog.handlers.events import RunBeforeStart
from milky_frog.harness.skills.catalog import SkillCatalog

_PROJECT_SKILLS_SUBDIR = Path(".milky-frog") / "skills"


class SkillCatalogHandler(BaseHandler):
    """Injects active Skills into the system prompt via ``RunBeforeStart``.

    On each new Run the handler builds a ``SkillCatalog`` for that workspace
    (project skills override user-level ones) and returns a
    ``SystemPromptSection`` containing all skill instructions.
    Malformed skill files are logged and skipped by ``SkillCatalog`` —
    they never abort the Run.

    Wiring (in ``MilkyFrog.__init__``):
        SkillCatalogHandler(settings.home / "skills").register(bus)
    """

    def __init__(self, user_skills_dir: Path) -> None:
        self._user_skills_dir = user_skills_dir

    def register(self, bus: LifecycleBus) -> None:
        bus.on(RunBeforeStart)(self._on_before_start)

    async def _on_before_start(
        self, event: RunBeforeStart, ctx: HandlerContext
    ) -> SystemPromptSection | None:
        del ctx
        project_skills_dir = event.workspace / _PROJECT_SKILLS_SUBDIR
        catalog = SkillCatalog(self._user_skills_dir, project_skills_dir)

        sections: list[str] = []
        for summary in catalog.summaries():
            instructions = catalog.load(summary.name).instructions
            if instructions:
                sections.append(f"### {summary.name}\n{instructions}")
        if not sections:
            return None
        header = "## Skills\n\nThe following project skills are active for this Run:"
        return SystemPromptSection(header + "\n\n" + "\n\n".join(sections))
