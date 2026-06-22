from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from milky_frog.project import PROJECT_DIRNAME

_CONTEXT_FILENAMES = ("AGENTS.md", "AGENTS.MD", "CLAUDE.md", "CLAUDE.MD")
_APPEND_FILENAME = "APPEND_SYSTEM.md"


@dataclass(frozen=True, slots=True)
class ContextFile:
    path: Path
    content: str


def load_context_files(workspace: Path, home: Path) -> tuple[ContextFile, ...]:
    """Load project instructions from the agent home and workspace ancestors."""
    resolved_workspace = workspace.expanduser().resolve()
    resolved_home = home.expanduser().resolve()
    files: list[ContextFile] = []
    seen: set[Path] = set()

    global_context = _load_context_file_from_dir(resolved_home)
    if global_context is not None:
        files.append(global_context)
        seen.add(global_context.path)

    ancestors: list[ContextFile] = []
    current = resolved_workspace
    root = Path("/").resolve()

    while True:
        context = _load_context_file_from_dir(current)
        if context is not None and context.path not in seen:
            ancestors.insert(0, context)
            seen.add(context.path)
        if current == root:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent

    files.extend(ancestors)
    return tuple(files)


def load_append_system_prompt(workspace: Path, home: Path) -> str | None:
    """Load optional global behavioral rules appended after the base prompt."""
    resolved_workspace = workspace.expanduser().resolve()
    resolved_home = home.expanduser().resolve()
    project_path = resolved_workspace / PROJECT_DIRNAME / _APPEND_FILENAME
    if project_path.is_file():
        return _read_text(project_path)
    global_path = resolved_home / _APPEND_FILENAME
    if global_path.is_file():
        return _read_text(global_path)
    return None


def _load_context_file_from_dir(directory: Path) -> ContextFile | None:
    for filename in _CONTEXT_FILENAMES:
        path = directory / filename
        if not path.is_file():
            continue
        content = _read_text(path)
        if content is None:
            continue
        return ContextFile(path.resolve(), content)
    return None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None
