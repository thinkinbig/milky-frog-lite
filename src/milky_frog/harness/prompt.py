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


@dataclass(frozen=True, slots=True)
class BuildSystemPromptOptions:
    workspace: Path
    home: Path
    context_files: tuple[ContextFile, ...] = ()
    append_system: str | None = None
    skill_locations: tuple[tuple[str, str, Path], ...] = ()
    extra_sections: tuple[str, ...] = ()


def build_system_prompt(options: BuildSystemPromptOptions) -> str:
    """Assemble the system prompt from the base identity and injected context."""
    workspace = options.workspace.expanduser().resolve()
    prompt_cwd = workspace.as_posix()

    prompt = _BASE_PROMPT
    if options.append_system:
        prompt += f"\n\n{options.append_system.strip()}"

    if options.context_files:
        prompt += format_project_context(options.context_files)

    if options.skill_locations:
        prompt += format_skills_for_prompt(options.skill_locations)

    if options.extra_sections:
        prompt += "\n\n" + "\n\n".join(section.strip() for section in options.extra_sections)

    prompt += f"\nCurrent date: {date.today().isoformat()}"
    prompt += f"\nCurrent working directory: {prompt_cwd}"
    return prompt


def system_prompt(workspace: Path, extra_sections: tuple[str, ...] = ()) -> str:
    """Build the stable system prompt for a Run.

    Agent-home context (instructions, append rules, skill catalog) is injected
    by ``AgentContextHandler`` via ``RunBeforeStart`` → ``extra_sections``.
    """
    workspace = workspace.expanduser().resolve()
    prompt = _BASE_PROMPT
    if extra_sections:
        prompt += "\n\n" + "\n\n".join(section.strip() for section in extra_sections)
    prompt += f"\nCurrent date: {date.today().isoformat()}"
    prompt += f"\nCurrent working directory: {workspace.as_posix()}"
    return prompt


def agent_context_section(workspace: Path, home: Path) -> str | None:
    """Build injectable context from agent home and workspace (for handlers)."""
    resolved_home = home.expanduser()
    parts: list[str] = []

    append = load_append_system_prompt(workspace, resolved_home)
    if append:
        parts.append(append.strip())

    context_files = load_context_files(workspace, resolved_home)
    if context_files:
        parts.append(format_project_context(context_files).strip())

    catalog = SkillCatalog(resolved_home / "skills", project_root(workspace) / "skills")
    skill_locations = catalog.prompt_locations()
    if skill_locations:
        parts.append(format_skills_for_prompt(skill_locations).strip())

    if not parts:
        return None
    return "\n\n".join(parts)


_BASE_PROMPT = """You are Milky Frog (奶蛙), a lightweight local coding agent.

Your identity is Milky Frog. When asked who or what you are, identify yourself as Milky Frog,
not as the underlying model or API provider. The provider is an implementation detail.

You complete one user goal at a time.

Be direct and technically precise. Use only the Tools supplied in the request. Never claim that
you inspected, changed, or executed something unless the available Tools allowed you to do so."""


def format_project_context(context_files: tuple[ContextFile, ...]) -> str:
    lines = [
        "\n\n<project_context>\n",
        "Project-specific instructions and guidelines:\n",
    ]
    for context in context_files:
        lines.extend(
            (
                f'<project_instructions path="{escape_attr(context.path.as_posix())}">\n',
                f"{context.content.strip()}\n",
                "</project_instructions>\n",
            )
        )
    lines.append("</project_context>\n")
    return "\n".join(lines)


def format_skills_for_prompt(
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


def escape_attr(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;")
