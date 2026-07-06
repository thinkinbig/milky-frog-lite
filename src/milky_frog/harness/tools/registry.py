from __future__ import annotations

import logging

from pydantic import JsonValue

from milky_frog.harness.tools.base import Tool

logger = logging.getLogger(__name__)


class UnknownToolError(LookupError):
    pass


class DuplicateToolError(ValueError):
    pass


class ToolRegistry:
    def __init__(self, tools: tuple[Tool, ...] = ()) -> None:
        self._builtin_names: frozenset[str] = frozenset(t.name for t in tools)
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise DuplicateToolError(tool.name)
        self._tools[tool.name] = tool

    def replace_mcp_tools(self, mcp_tools: tuple[Tool, ...]) -> None:
        """Remove all previously registered MCP tools and add the new set.

        Builtin tools (those passed to ``__init__``) are always preserved.
        """
        for name in [n for n in self._tools if n not in self._builtin_names]:
            del self._tools[name]
        for tool in mcp_tools:
            try:
                self.register(tool)
            except DuplicateToolError:
                logger.warning("MCP tool %r conflicts with a builtin tool; skipping", tool.name)

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as error:
            raise UnknownToolError(name) from error

    def tools(self) -> tuple[Tool, ...]:
        return tuple(self._tools.values())

    def schemas(self) -> tuple[dict[str, JsonValue], ...]:
        return tuple(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_model.model_json_schema(),
                },
            }
            for tool in self._tools.values()
        )
