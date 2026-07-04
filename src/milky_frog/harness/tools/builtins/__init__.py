from __future__ import annotations

from milky_frog.harness.tools.base import Tool
from milky_frog.harness.tools.builtins.bash import BashTool
from milky_frog.harness.tools.builtins.edit import EditFileTool
from milky_frog.harness.tools.builtins.fetch import FetchTool
from milky_frog.harness.tools.builtins.grep import GrepTool
from milky_frog.harness.tools.builtins.list_dir import ListDirTool
from milky_frog.harness.tools.builtins.read import ReadFileTool
from milky_frog.harness.tools.builtins.write import WriteFileTool


def default_tools() -> tuple[Tool, ...]:
    """The built-in Tools wired into every Run by default."""
    tools: tuple[Tool, ...] = (
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        ListDirTool(),
        GrepTool(),
        BashTool(),
        FetchTool(),
    )
    return tools


__all__ = [
    "BashTool",
    "EditFileTool",
    "FetchTool",
    "GrepTool",
    "ListDirTool",
    "ReadFileTool",
    "WriteFileTool",
    "default_tools",
]
