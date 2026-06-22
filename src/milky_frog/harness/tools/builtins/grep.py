from __future__ import annotations

import asyncio
import os
import re as re_mod

from pydantic import BaseModel, Field

from milky_frog.domain import ToolResult
from milky_frog.harness.sandbox.base import Sandbox
from milky_frog.harness.tools.base import ToolContext

_RG_TIMEOUT_SECONDS = 15.0
_MAX_OUTPUT_BYTES = 128 * 1024


class GrepInput(BaseModel):
    pattern: str = Field(
        description="Regex pattern to search for, e.g. 'def _execute' or 'class Tool'.",
    )
    path: str = Field(
        default=".",
        description=(
            "Workspace-relative directory or file to search in. Defaults to the workspace root."
        ),
    )


class GrepTool:
    """Search file contents with ripgrep, falling back to Python re if rg is unavailable."""

    name = "grep"
    requires_approval = False
    description = (
        "Search file contents in the workspace with a regex pattern using ripgrep (rg). "
        "Returns matching lines with file paths and line numbers. "
        "Use this before reading files — search for a class/function name or keyword first, "
        "then read only the files that match."
    )
    input_model: type[BaseModel] = GrepInput

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        params = GrepInput.model_validate(input)
        pattern = params.pattern.strip()
        if not pattern:
            return ToolResult("empty grep pattern", is_error=True)

        sandbox = context.require_sandbox()
        search_dir = str(sandbox.workspace / params.path)
        # Prefix to make paths workspace-relative when params.path != "."
        path_prefix = params.path if params.path != "." else ""

        try:
            return await self._rg_grep(pattern, search_dir, path_prefix, sandbox)
        except FileNotFoundError:
            return self._re_fallback(pattern, search_dir, path_prefix)

    async def _rg_grep(
        self, pattern: str, search_dir: str, path_prefix: str, sandbox: Sandbox
    ) -> ToolResult:
        env = sandbox.command_environment()
        args = [
            "rg",
            "--no-heading",
            "-n",
            "--color",
            "never",
            "-M",
            "300",
            pattern,
            search_dir,
        ]
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=_RG_TIMEOUT_SECONDS
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return ToolResult(f"rg timed out after {_RG_TIMEOUT_SECONDS}s", is_error=True)

        rc = process.returncode or 0

        # rg exit codes: 0 = matches found, 1 = no matches, 2 = error
        if rc >= 2:
            error_text = stderr_bytes.decode("utf-8", errors="replace").strip() or "(no stderr)"
            return ToolResult(f"rg error (exit {rc}): {error_text}", is_error=True)

        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        if not stdout_text.strip():
            return ToolResult("(no matches)")

        if path_prefix:
            stdout_text = self._rewrite_paths(stdout_text, path_prefix)

        if len(stdout_text) > _MAX_OUTPUT_BYTES:
            truncated = stdout_text[:_MAX_OUTPUT_BYTES]
            msg = (
                f"output truncated ({len(stdout_text)} bytes; "
                f"showing first {_MAX_OUTPUT_BYTES}):\n{truncated}"
            )
            return ToolResult(msg)
        return ToolResult(stdout_text)

    @staticmethod
    def _rewrite_paths(text: str, prefix: str) -> str:
        """Prepend *prefix* to the file-path part of every rg output line.

        rg outputs paths relative to the search directory, but the rest of the
        tool set expects workspace-relative paths.  ``read_file("a.py")`` fails
        when the file is actually at ``src/a.py``.
        """
        rewritten: list[str] = []
        for line in text.splitlines():
            idx = line.find(":")
            if idx > 0:
                rewritten.append(f"{prefix}/{line}")
            else:
                rewritten.append(line)
        return "\n".join(rewritten)

    def _re_fallback(self, pattern: str, search_dir: str, path_prefix: str) -> ToolResult:
        """Fallback grep using Python's re module when rg is unavailable."""
        try:
            regex = re_mod.compile(pattern)
        except re_mod.error as exc:
            return ToolResult(f"invalid regex pattern: {exc}", is_error=True)

        if os.path.isfile(search_dir):
            filepaths: list[str] = [search_dir]
        else:
            filepaths = self._collect_filepaths(search_dir)

        results: list[str] = []
        total_bytes = 0

        for filepath in filepaths:
            relpath = os.path.relpath(filepath, search_dir)
            if path_prefix:
                relpath = f"{path_prefix}/{relpath}"

            try:
                with open(filepath, encoding="utf-8", errors="replace") as f:
                    for line_num, line in enumerate(f, start=1):
                        if regex.search(line):
                            line_str = f"{relpath}:{line_num}:{line.rstrip()}\n"
                            line_bytes = len(line_str.encode("utf-8"))
                            if total_bytes + line_bytes > _MAX_OUTPUT_BYTES:
                                results.append(
                                    "output truncated"
                                    f" ({total_bytes + line_bytes} bytes;"
                                    f" showing first {_MAX_OUTPUT_BYTES}):\n"
                                )
                                return ToolResult("".join(results))
                            results.append(line_str)
                            total_bytes += line_bytes
            except OSError:
                continue

        if not results:
            return ToolResult("(no matches)")
        return ToolResult("".join(results))

    @staticmethod
    def _collect_filepaths(search_dir: str) -> list[str]:
        """Walk a directory tree and return sorted file paths."""
        filepaths: list[str] = []
        for root, dirs, files in os.walk(search_dir):
            dirs.sort()
            for filename in sorted(files):
                filepaths.append(os.path.join(root, filename))
        return filepaths
