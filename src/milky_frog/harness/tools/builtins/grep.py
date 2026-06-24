from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel, Field

from milky_frog.domain import ToolResult
from milky_frog.harness.sandbox import Sandbox, SandboxViolation
from milky_frog.harness.tools.base import ToolContext
from milky_frog.harness.tools.truncate import truncate_tool_output

# Cap a single matched line so one minified file can't dominate the output.
_MAX_LINE_CHARS = 300
# Stop scanning once collected output would exceed the truncation budget.
_MAX_OUTPUT_CHARS = 32000


class GrepInput(BaseModel):
    pattern: str = Field(description="Regex to search for, e.g. 'def _execute' or 'class Tool'.")
    path: str = Field(
        default=".",
        description="Workspace-relative directory or file to search. Defaults to the root.",
    )


class GrepTool:
    """Search workspace file contents for a regex, returning ``path:line:text``.

    Pure-Python search (no external ``rg`` dependency).  Every file searched is
    run through ``Sandbox.resolve`` first, so denied/escaping paths
    (``.env``, ``.git/**``, …) are skipped — exactly the paths ``read_file``
    would also refuse.  That shared policy is why this tool is approval-free and
    why its output always composes with ``read_file``.

    It honours the sandbox deny-patterns but *not* ``.gitignore``; vendored
    trees (``node_modules`` …) are searched.  Scope the ``path`` to stay fast.
    """

    name = "grep"
    requires_approval = False
    description = (
        "Search workspace file contents for a regex pattern. Returns matching lines as "
        "path:line:text (workspace-relative paths). "
        "Scope `path` to a subdirectory to stay fast; sensitive paths "
        "(.env, .git, keys) are skipped."
    )
    input_model: type[BaseModel] = GrepInput

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        params = GrepInput.model_validate(input)
        pattern = params.pattern.strip()
        if not pattern:
            return ToolResult("empty grep pattern", is_error=True)
        try:
            regex = re.compile(pattern)
        except re.error as error:
            return ToolResult(f"invalid regex: {error}", is_error=True)

        sandbox = context.require_sandbox()
        try:
            root = sandbox.resolve(params.path)
        except SandboxViolation as error:
            return ToolResult(str(error), is_error=True)
        if not root.exists():
            return ToolResult(f"not found: {params.path}", is_error=True)

        lines: list[str] = []
        total = 0
        for file in _iter_allowed_files(sandbox, root):
            rel = file.relative_to(sandbox.workspace).as_posix()
            try:
                text = file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue  # unreadable or binary — skip
            for line_no, line in enumerate(text.splitlines(), start=1):
                if not regex.search(line):
                    continue
                rendered = f"{rel}:{line_no}:{line[:_MAX_LINE_CHARS]}"
                lines.append(rendered)
                total += len(rendered) + 1
                if total > _MAX_OUTPUT_CHARS:
                    break
            if total > _MAX_OUTPUT_CHARS:
                break

        if not lines:
            return ToolResult("(no matches)")
        output = truncate_tool_output(
            "\n".join(lines), max_chars=_MAX_OUTPUT_CHARS, tool_name="grep"
        )
        return ToolResult(output)


def _iter_allowed_files(sandbox: Sandbox, root: Path) -> Iterator[Path]:
    """Yield text-file candidates under *root* that pass the sandbox policy.

    Reuses ``sandbox.resolve`` as the single allow/deny predicate, pruning denied
    directories (``.git`` …) during the walk so we never descend into them.
    """
    if root.is_file():
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if _is_allowed(sandbox, Path(dirpath, d)))
        for name in sorted(filenames):
            candidate = Path(dirpath, name)
            if _is_allowed(sandbox, candidate):
                yield candidate


def _is_allowed(sandbox: Sandbox, path: Path) -> bool:
    try:
        rel = path.relative_to(sandbox.workspace).as_posix()
    except ValueError:
        return False
    try:
        sandbox.resolve(rel)
    except SandboxViolation:
        return False
    return True
