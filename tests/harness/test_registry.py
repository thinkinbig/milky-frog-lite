from __future__ import annotations

from pydantic import BaseModel

from milky_frog.domain import ToolResult
from milky_frog.harness.tools import ToolContext, ToolRegistry
from milky_frog.harness.tools.base import Tool


class _NoInput(BaseModel):
    pass


class _StubTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = "stub"
        self.input_model: type[BaseModel] = _NoInput

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        return ToolResult(self.name)


def _builtin(name: str) -> Tool:
    return _StubTool(name)


def test_replace_mcp_tools_returns_count_of_registered_tools() -> None:
    registry = ToolRegistry((_builtin("grep"),))

    registered = registry.replace_mcp_tools((_StubTool("server__a"), _StubTool("server__b")))

    assert registered == 2
    assert {t.name for t in registry.tools()} == {"grep", "server__a", "server__b"}


def test_replace_mcp_tools_excludes_builtin_conflicts_from_count() -> None:
    registry = ToolRegistry((_builtin("grep"),))

    registered = registry.replace_mcp_tools((_StubTool("grep"), _StubTool("server__b")))

    assert registered == 1
    names = {t.name for t in registry.tools()}
    assert names == {"grep", "server__b"}
    assert registry.get("grep") is not None


def test_replace_mcp_tools_drops_previous_mcp_tools_on_reload() -> None:
    registry = ToolRegistry((_builtin("grep"),))
    registry.replace_mcp_tools((_StubTool("old__tool"),))

    registered = registry.replace_mcp_tools((_StubTool("new__tool"),))

    assert registered == 1
    names = {t.name for t in registry.tools()}
    assert names == {"grep", "new__tool"}
