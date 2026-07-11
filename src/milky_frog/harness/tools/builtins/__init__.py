from __future__ import annotations

from milky_frog.harness.tools.base import Tool
from milky_frog.harness.tools.builtins.bash import BashTool
from milky_frog.harness.tools.builtins.edit import EditFileTool
from milky_frog.harness.tools.builtins.fetch import FetchTool
from milky_frog.harness.tools.builtins.grep import GrepTool
from milky_frog.harness.tools.builtins.list_dir import ListDirTool
from milky_frog.harness.tools.builtins.read import ReadFileTool
from milky_frog.harness.tools.builtins.subagent import SubagentRunner, SubagentTool
from milky_frog.harness.tools.builtins.web_search import WebSearchTool
from milky_frog.harness.tools.builtins.write import WriteFileTool


def default_tools(*, jina_api_key: str | None = None) -> tuple[Tool, ...]:
    """The built-in Tools wired into every Run by default.

    ``jina_api_key`` (from ``MILKY_FROG_JINA_API_KEY``) is optional: it lets
    ``fetch`` retry blocked requests via Jina Reader, and gates ``web_search``
    entirely — without a key, web_search is omitted rather than registered in
    a broken state.
    """
    tools: list[Tool] = [
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        ListDirTool(),
        GrepTool(),
        BashTool(),
        FetchTool(jina_api_key=jina_api_key),
    ]
    if jina_api_key:
        tools.append(WebSearchTool(jina_api_key))
    return tuple(tools)


def read_only_tools(*, jina_api_key: str | None = None) -> tuple[Tool, ...]:
    """The read-only subset of ``default_tools`` for a nested ``subagent`` Run.

    Excludes ``write_file``/``edit_file``/``bash`` (no write surface, so no
    worktree isolation is needed) and ``subagent`` itself (caps nesting at one
    level by construction).
    """
    tools: list[Tool] = [
        ReadFileTool(),
        ListDirTool(),
        GrepTool(),
        FetchTool(jina_api_key=jina_api_key),
    ]
    if jina_api_key:
        tools.append(WebSearchTool(jina_api_key))
    return tuple(tools)


__all__ = [
    "BashTool",
    "EditFileTool",
    "FetchTool",
    "GrepTool",
    "ListDirTool",
    "ReadFileTool",
    "SubagentRunner",
    "SubagentTool",
    "WebSearchTool",
    "WriteFileTool",
    "default_tools",
    "read_only_tools",
]
