from __future__ import annotations

import contextlib
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict

from milky_frog.domain import ToolResult
from milky_frog.harness.mcp.config import McpServerConfig
from milky_frog.harness.tools.base import ToolContext

logger = logging.getLogger(__name__)


def _make_input_model(schema: dict[str, Any]) -> type[BaseModel]:
    """Create a passthrough Pydantic model that surfaces the original MCP JSON Schema.

    Uses ``json_schema_extra`` to replace the auto-generated schema with the
    original MCP inputSchema, so ``ToolRegistry.schemas()`` sends the correct
    definition to the model without any method-override machinery.
    """
    _schema = schema

    def _replace_schema(s: dict[str, Any]) -> None:
        s.clear()
        s.update(_schema)

    class McpInput(BaseModel):
        model_config = ConfigDict(extra="allow", json_schema_extra=_replace_schema)

    return McpInput


class McpTool:
    """Adapts one MCP server tool to the Tool protocol."""

    def __init__(
        self,
        name: str,
        description: str,
        input_model: type[BaseModel],
        session: Any,
        mcp_name: str,
    ) -> None:
        self.name = name
        self.description = description
        self.input_model = input_model
        self._session = session
        self._mcp_name = mcp_name

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        from mcp.types import TextContent

        arguments: dict[str, Any] = input.model_dump()
        try:
            result = await self._session.call_tool(self._mcp_name, arguments)
        except Exception as exc:
            return ToolResult(f"MCP tool error: {exc}", is_error=True)

        parts: list[str] = []
        for item in result.content:
            if isinstance(item, TextContent):
                parts.append(item.text)
            else:
                parts.append(f"[{type(item).__name__}]")

        text = "\n".join(parts) if parts else "(no output)"
        return ToolResult(text, is_error=bool(result.isError))


class McpClientManager:
    """Manages per-server MCP connections with independent lifecycles.

    Each server gets its own ``AsyncExitStack`` so individual servers can be
    connected and disconnected without affecting others.
    """

    def __init__(self) -> None:
        # name → (per-server exit stack, tools from that server)
        self._servers: dict[str, tuple[contextlib.AsyncExitStack, list[McpTool]]] = {}

    @property
    def running_servers(self) -> frozenset[str]:
        return frozenset(self._servers)

    @property
    def tools(self) -> list[McpTool]:
        return [tool for _, tools in self._servers.values() for tool in tools]

    async def __aenter__(self) -> McpClientManager:
        return self

    async def connect_server(self, name: str, cfg: McpServerConfig) -> list[McpTool]:
        """Connect one server. Disconnects first if it's already running."""
        if name in self._servers:
            await self.disconnect_server(name)
        stack = contextlib.AsyncExitStack()
        await stack.__aenter__()
        try:
            tools = await self._connect(name, cfg, stack)
        except Exception:
            await stack.aclose()
            raise
        self._servers[name] = (stack, tools)
        return tools

    async def disconnect_server(self, name: str) -> None:
        """Disconnect one server and remove its tools."""
        entry = self._servers.pop(name, None)
        if entry is None:
            return
        stack, _ = entry
        try:
            await stack.aclose()
        except Exception:
            logger.warning("error closing MCP server %r", name, exc_info=True)

    async def _connect(
        self, server_name: str, cfg: McpServerConfig, stack: contextlib.AsyncExitStack
    ) -> list[McpTool]:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=cfg.command,
            args=list(cfg.args),
            env=dict(cfg.env) if cfg.env else None,
        )
        read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        listed = await session.list_tools()
        tools: list[McpTool] = []
        for mcp_tool in listed.tools:
            tool_name = f"{server_name}__{mcp_tool.name}"
            description = mcp_tool.description or f"{mcp_tool.name} (from {server_name})"
            input_model = _make_input_model(dict(mcp_tool.inputSchema))
            tools.append(
                McpTool(
                    name=tool_name,
                    description=description,
                    input_model=input_model,
                    session=session,
                    mcp_name=mcp_tool.name,
                )
            )

        logger.info("connected to MCP server %r: %d tool(s)", server_name, len(tools))
        return tools

    async def __aexit__(self, *args: Any) -> None:
        for name in list(self._servers):
            await self.disconnect_server(name)
