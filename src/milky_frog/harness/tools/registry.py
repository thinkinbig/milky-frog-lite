from __future__ import annotations

from pydantic import JsonValue

from milky_frog.domain import ToolCall
from milky_frog.harness.tools.base import Tool


class UnknownToolError(LookupError):
    pass


class DuplicateToolError(ValueError):
    pass


def approval_free_tool_names(tools: tuple[Tool, ...]) -> frozenset[str]:
    """Return tool names that never need approval (``requires_approval`` is false)."""
    return frozenset(
        tool.name for tool in tools if not getattr(tool, "requires_approval", True)
    )


def call_needs_approval(tool: Tool, call: ToolCall) -> bool:
    """Return whether a concrete tool call should pause for user approval."""
    if getattr(tool, "requires_approval", True) is False:
        return False
    per_call = getattr(tool, "needs_approval_for_call", None)
    if per_call is not None:
        return bool(per_call(call.arguments))
    return True


class ToolRegistry:
    def __init__(self, tools: tuple[Tool, ...] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise DuplicateToolError(tool.name)
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as error:
            raise UnknownToolError(name) from error

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
