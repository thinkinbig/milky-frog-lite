from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from milky_frog.harness.prompt_context import (
    ContextFile,
    load_append_system_prompt,
    load_context_files,
)
from milky_frog.harness.skills import SkillCatalog
from milky_frog.project import project_root

_DEFAULT_HOME = Path.home() / ".milky-frog"


@dataclass(frozen=True, slots=True)
class BuildSystemPromptOptions:
    workspace: Path
    home: Path
    context_files: tuple[ContextFile, ...] = ()
    append_system: str | None = None
    skill_locations: tuple[tuple[str, str, Path], ...] = ()


def build_system_prompt(options: BuildSystemPromptOptions) -> str:
    """Assemble the system prompt from the base identity and injected context."""
    workspace = options.workspace.expanduser().resolve()
    prompt_cwd = workspace.as_posix()

    prompt = _BASE_PROMPT
    if options.append_system:
        prompt += f"\n\n{options.append_system.strip()}"

    if options.context_files:
        prompt += "\n\n<project_context>\n\n"
        prompt += "Project-specific instructions and guidelines:\n\n"
        for context in options.context_files:
            prompt += (
                f'<project_instructions path="{_escape_attr(context.path.as_posix())}">\n'
                f"{context.content.strip()}\n"
                "</project_instructions>\n\n"
            )
        prompt += "</project_context>\n"

    if options.skill_locations:
        prompt += _format_skills_for_prompt(options.skill_locations)

    prompt += f"\nCurrent date: {date.today().isoformat()}"
    prompt += f"\nCurrent working directory: {prompt_cwd}"
    return prompt


def system_prompt(workspace: Path, *, home: Path | None = None) -> str:
    """Build the system prompt for a Run from workspace-local and global sources."""
    resolved_home = (home or _DEFAULT_HOME).expanduser()
    catalog = SkillCatalog(resolved_home / "skills", project_root(workspace) / "skills")
    return build_system_prompt(
        BuildSystemPromptOptions(
            workspace=workspace,
            home=resolved_home,
            context_files=load_context_files(workspace, resolved_home),
            append_system=load_append_system_prompt(workspace, resolved_home),
            skill_locations=catalog.prompt_locations(),
        )
    )


_BASE_PROMPT = """You are Milky Frog (奶蛙), a lightweight local senior coding agent export.

Your identity is Milky Frog. When asked who or what you are, identify yourself as Milky Frog,
not as the underlying model or API provider. The provider is an implementation detail.

You complete one user goal at a time.

Be direct and technically precise. Use only the Tools supplied in the request. Never claim that
you inspected, changed, or executed something unless the available Tools allowed you to do so."""


def _format_skills_for_prompt(
    skills: tuple[tuple[str, str, Path], ...],
) -> str:
    lines = [
        "\n\nThe following skills provide specialized instructions for specific tasks.",
        "Use read_file to load a skill's SKILL.md when the task matches its description.",
        "When a skill file references a relative path, resolve it against the skill directory.",
        "",
        "<available_skills>",
    ]
    for name, description, location in skills:
        lines.extend(
            (
                "  <skill>",
                f"    <name>{escape(name)}</name>",
                f"    <description>{escape(description)}</description>",
                f"    <location>{escape(location.resolve().as_posix())}</location>",
                "  </skill>",
            )
        )
    lines.append("</available_skills>")
    return "\n".join(lines)


def _escape_attr(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;")
