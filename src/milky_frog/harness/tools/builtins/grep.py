from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel, Field

from milky_frog.core.sandbox import Sandbox, SandboxViolation
from milky_frog.domain import ToolResult
from milky_frog.harness.tools.base import ToolContext
from milky_frog.harness.tools.spill import SPILL_DIR
from milky_frog.harness.tools.truncate import truncate_tool_output


class GrepInput(BaseModel):
    pattern: str = Field(description="Regex to search for, e.g. 'def _execute' or 'class Tool'.")
    path: str = Field(
        default=".",
        description="Workspace-relative directory or file to search. Defaults to the root.",
    )
    context: int = Field(
        default=0,
        ge=0,
        le=20,
        description=(
            "Lines of surrounding context to show before and after each match (like grep -C). "
            "Use a few lines to read the match in place instead of a follow-up read_file."
        ),
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

        search_max = sandbox.config.search_output_max_chars
        out: list[str] = []
        for file in _iter_allowed_files(sandbox, root):
            rel = file.relative_to(sandbox.workspace).as_posix()
            try:
                text = file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue  # unreadable or binary — skip
            file_lines = text.splitlines()
            match_nums = [n for n, line in enumerate(file_lines, start=1) if regex.search(line)]
            if not match_nums:
                continue
            if out and params.context > 0:
                out.append("--")  # separate files when context lines are shown
            out.extend(_render_matches(rel, file_lines, match_nums, params.context))

        if not out:
            return ToolResult("(no matches)")
        output = truncate_tool_output(
            "\n".join(out),
            max_chars=search_max,
            workspace=sandbox.workspace,
            label="grep",
            counter=context.token_counter,
        )
        return ToolResult(output)


def _render_matches(
    rel: str, file_lines: list[str], match_nums: list[int], context: int
) -> list[str]:
    """Render one file's matches as ``rel:line:text`` (``-`` separators for context lines).

    With ``context == 0`` this yields exactly one ``rel:line:text`` per match — the
    historical format. With context, overlapping windows are merged and
    non-contiguous groups are separated by ``--`` (grep -C convention).
    """
    total_lines = len(file_lines)
    match_set = set(match_nums)
    intervals: list[tuple[int, int]] = []
    for n in match_nums:
        lo = max(1, n - context)
        hi = min(total_lines, n + context)
        if intervals and lo <= intervals[-1][1] + 1:
            intervals[-1] = (intervals[-1][0], max(intervals[-1][1], hi))
        else:
            intervals.append((lo, hi))

    out: list[str] = []
    for idx, (lo, hi) in enumerate(intervals):
        if idx > 0 and context > 0:
            out.append("--")
        for ln in range(lo, hi + 1):
            sep = ":" if ln in match_set else "-"
            out.append(f"{rel}{sep}{ln}{sep}{file_lines[ln - 1]}")
    return out


def _iter_allowed_files(sandbox: Sandbox, root: Path) -> Iterator[Path]:
    """Yield text-file candidates under *root* that pass the sandbox policy.

    Reuses ``sandbox.resolve`` as the single allow/deny predicate, pruning denied
    directories (``.git`` …) during the walk so we never descend into them. The
    spill directory is pruned too: it is readable by ``read_file`` (for recovery)
    but searching it would surface the tool's own truncated outputs as matches.
    """
    if root.is_file():
        yield root
        return
    spill_dir = sandbox.workspace / SPILL_DIR
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d
            for d in dirnames
            if _is_allowed(sandbox, Path(dirpath, d)) and Path(dirpath, d) != spill_dir
        )
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
