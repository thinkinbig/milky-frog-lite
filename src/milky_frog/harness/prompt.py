from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from milky_frog.harness.prompt_context import (
    AgentContext,
    ContextFile,
    ContextLoader,
    load_agent_context,
)

__all__ = [
    "BuildSystemPromptOptions",
    "ContextLoader",
    "agent_context_section",
    "build_system_prompt",
    "format_agent_context",
    "format_project_context",
    "format_skills_for_prompt",
    "load_agent_context",
    "make_context_loader",
    "system_prompt",
]


@dataclass(frozen=True, slots=True)
class BuildSystemPromptOptions:
    workspace: Path
    agent_context: AgentContext | None = None
    extra_sections: tuple[str, ...] = ()


def build_system_prompt(options: BuildSystemPromptOptions) -> str:
    """Assemble the full system prompt from base identity, loaded context, and extras."""
    workspace = options.workspace.expanduser().resolve()
    prompt = _BASE_PROMPT
    if options.agent_context is not None:
        prompt = _append_agent_context(prompt, options.agent_context)
    if options.extra_sections:
        prompt += "\n\n" + "\n\n".join(section.strip() for section in options.extra_sections)
    prompt += f"\nCurrent date: {date.today().isoformat()}"
    prompt += f"\nCurrent working directory: {workspace.as_posix()}"
    return prompt


def system_prompt(workspace: Path, extra_sections: tuple[str, ...] = ()) -> str:
    """Build the system prompt for a Run.

    Handler-injected context arrives via ``RunBeforeStart`` → ``extra_sections``.
    Use ``build_system_prompt`` with ``load_agent_context`` when assembling the
    full prompt in one step (tests, tooling).
    """
    return build_system_prompt(
        BuildSystemPromptOptions(workspace=workspace, extra_sections=extra_sections)
    )


def agent_context_section(workspace: Path, home: Path) -> str | None:
    """Format loaded agent-home context as a single system-prompt section."""
    return format_agent_context(load_agent_context(workspace, home))


def make_context_loader(home: Path) -> ContextLoader:
    """Return a ``ContextLoader`` bound to the given agent home directory."""

    def _load(workspace: Path) -> str | None:
        return agent_context_section(workspace, home)

    return _load


def format_agent_context(context: AgentContext) -> str | None:
    """Format structured agent context as one injectable section."""
    parts = _agent_context_parts(context)
    if not parts:
        return None
    return "\n\n".join(parts)


def _append_agent_context(prompt: str, context: AgentContext) -> str:
    for part in _agent_context_parts(context):
        prompt += f"\n\n{part}"
    return prompt


def _agent_context_parts(context: AgentContext) -> tuple[str, ...]:
    parts: list[str] = []
    if context.append_system:
        parts.append(context.append_system.strip())
    if context.context_files:
        parts.append(format_project_context(context.context_files).strip())
    if context.skill_locations:
        parts.append(format_skills_for_prompt(context.skill_locations).strip())
    return tuple(parts)


_BASE_PROMPT = """You are Milky Frog (奶蛙), a lightweight local coding agent.

Your identity is Milky Frog. When asked who or what you are, identify yourself as Milky Frog,
not as the underlying model or API provider. The provider is an implementation detail.

You complete one user goal at a time.

Be direct and technically precise. Use only the Tools supplied in the request. Never claim that
you inspected, changed, or executed something unless the available Tools allowed you to do so.

<rules>
  <rule>When exploring with Tools, prefer narrow commands over broad ones. Start with the
  smallest scope that can answer the question — one file or directory, summary flags
  (--stat, --numstat), or head/tail/wc pipes — before running repo-wide diffs, greps, or
  listings.</rule>
  <rule>Locate before reading. Use grep (with a few context lines) or list_dir to find the
  relevant file and line, then read a narrow window with read_file's offset/limit rather
  than whole files. A casual or exploratory question does not justify a repo-wide scan.</rule>
  <rule>Do not re-read a file already shown earlier in the conversation, and do not call
  read_file on a path you have not confirmed is a file — use list_dir first when unsure.</rule>
  <rule>Tool output may be truncated. If you see a truncation notice, refine the command
  instead of repeating it at the same scope.</rule>
  <rule>Simple tasks do not need to be split across many steps.</rule>
</rules>"""


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
