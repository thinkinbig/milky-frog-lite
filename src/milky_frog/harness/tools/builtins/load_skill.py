from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from milky_frog.domain import ToolResult
from milky_frog.harness.skills import SkillCatalog
from milky_frog.harness.tools.base import ToolContext
from milky_frog.project import project_root


class LoadSkillInput(BaseModel):
    name: str = Field(description="Name of the skill to load, as advertised in available_skills.")


class LoadSkillTool:
    """Load a Skill's instructions by name, bypassing the Sandbox path resolver.

    Skills are Harness resources, not Workspace files: bundled ones live in the
    milky-frog source tree, so a ``read_file`` on their host-absolute path is
    denied by the Sandbox. Loading by name goes through ``SkillCatalog.load``
    and works for bundled, user, and project Skills alike.
    """

    name = "load_skill"
    requires_approval = False
    description = (
        "Load the full instructions for a skill by name. Use this instead of read_file when a "
        "task matches a skill advertised in available_skills."
    )
    input_model: type[BaseModel] = LoadSkillInput

    def __init__(self, home: Path) -> None:
        self._home = home.expanduser()

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        params = LoadSkillInput.model_validate(input)
        catalog = SkillCatalog(self._home / "skills", project_root(context.workspace) / "skills")
        try:
            skill = catalog.load(params.name)
        except KeyError:
            available = ", ".join(summary.name for summary in catalog.summaries())
            return ToolResult(
                f"unknown skill: {params.name}. Available skills: {available}",
                is_error=True,
            )
        except (OSError, ValueError) as error:
            return ToolResult(f"{type(error).__name__}: {error}", is_error=True)
        return ToolResult(
            f'<skill name="{skill.summary.name}" '
            f'directory="{skill.path.parent.as_posix()}">\n'
            f"{skill.instructions}\n"
            "</skill>"
        )
