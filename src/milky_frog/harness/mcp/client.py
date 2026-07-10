from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from milky_frog.domain import ToolResult
from milky_frog.harness.mcp.config import McpServerConfig
from milky_frog.harness.tools.base import ToolContext

logger = logging.getLogger(__name__)

# Minimal set of parent-process variables an MCP server subprocess needs to
# launch (package-manager runners like npx/uvx resolve interpreters and caches
# via these). Anything else — including Milky Frog's own API keys — must be
# opted into explicitly via the server's ``env`` config, never inherited.
_MCP_SAFE_ENV_VARS = ("PATH", "HOME", "USER", "SHELL", "LANG", "LC_ALL", "TMPDIR")


def _build_server_env(cfg: McpServerConfig) -> dict[str, str]:
    env = {name: os.environ[name] for name in _MCP_SAFE_ENV_VARS if name in os.environ}
    env.update(cfg.env)
    return env


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
        required: tuple[str, ...] = (),
    ) -> None:
        self.name = name
        self.description = description
        self.input_model = input_model
        self._session = session
        self._mcp_name = mcp_name
        self._required = required

    async def execute(self, context: ToolContext, input: BaseModel) -> ToolResult:
        from mcp.types import TextContent

        arguments: dict[str, Any] = input.model_dump()
        missing = [field for field in self._required if field not in arguments]
        if missing:
            return ToolResult(f"Missing required argument(s): {', '.join(missing)}", is_error=True)
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


@dataclass(frozen=True, slots=True)
class _ConnectedServer:
    """One server's task-affine transport and its discovered Tools."""

    stack: contextlib.AsyncExitStack
    tools: list[McpTool]


@dataclass(frozen=True, slots=True)
class _ConnectServer:
    name: str
    cfg: McpServerConfig
    completion: asyncio.Future[list[McpTool]]


@dataclass(frozen=True, slots=True)
class _DisconnectServer:
    name: str
    completion: asyncio.Future[None]


@dataclass(frozen=True, slots=True)
class _Shutdown:
    completion: asyncio.Future[None]


type _McpCommand = _ConnectServer | _DisconnectServer | _Shutdown


class McpClientManager:
    """Manages per-server MCP connections with independent lifecycles.

    A single supervisor Task owns every stdio context. This ensures anyio
    cancel scopes created by ``stdio_client()`` are exited by the same Task that
    entered them, even when a reload or shutdown originates in another Task.
    """

    def __init__(self) -> None:
        self._servers: dict[str, _ConnectedServer] = {}
        self._commands: asyncio.Queue[_McpCommand] = asyncio.Queue()
        self._supervisor: asyncio.Task[None] | None = None

    @property
    def running_servers(self) -> frozenset[str]:
        return frozenset(self._servers)

    @property
    def tools(self) -> list[McpTool]:
        return [tool for server in self._servers.values() for tool in server.tools]

    async def __aenter__(self) -> McpClientManager:
        self._ensure_supervisor()
        return self

    async def connect_server(self, name: str, cfg: McpServerConfig) -> list[McpTool]:
        """Connect one server through the task that owns stdio contexts."""
        self._ensure_supervisor()
        ready: asyncio.Future[list[McpTool]] = asyncio.get_running_loop().create_future()
        self._commands.put_nowait(_ConnectServer(name, cfg, ready))
        return await ready

    async def connect_many(self, servers: dict[str, McpServerConfig]) -> None:
        """Connect several independent servers without one failure blocking the rest.

        Connections are deliberately serial: MCP setup is low-frequency, and
        the supervisor must own every stdio context for its entire lifetime.
        """
        for name, cfg in servers.items():
            try:
                await self.connect_server(name, cfg)
            except Exception:
                logger.warning("failed to connect to MCP server %r; skipping", name, exc_info=True)

    async def disconnect_server(self, name: str) -> None:
        """Disconnect one server and remove its tools."""
        self._ensure_supervisor()
        complete: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._commands.put_nowait(_DisconnectServer(name, complete))
        await complete

    def _ensure_supervisor(self) -> None:
        if self._supervisor is None:
            self._supervisor = asyncio.create_task(self._serve())

    async def _serve(self) -> None:
        """Process lifecycle changes in the Task that owns all stdio contexts."""
        while True:
            command = await self._commands.get()
            if isinstance(command, _ConnectServer):
                await self._handle_connect(command)
            elif isinstance(command, _DisconnectServer):
                await self._handle_disconnect(command)
            else:
                await self._handle_shutdown(command)
                return

    async def _handle_connect(self, command: _ConnectServer) -> None:
        await self._close_server(command.name)
        stack = contextlib.AsyncExitStack()
        try:
            await stack.__aenter__()
            tools = await self._connect(command.name, command.cfg, stack)
        except Exception as error:
            try:
                await stack.aclose()
            except Exception:
                logger.warning(
                    "error closing MCP server %r after connection failure",
                    command.name,
                    exc_info=True,
                )
            if not command.completion.done():
                command.completion.set_exception(error)
        else:
            self._servers[command.name] = _ConnectedServer(stack, tools)
            if not command.completion.done():
                command.completion.set_result(tools)

    async def _handle_disconnect(self, command: _DisconnectServer) -> None:
        await self._close_server(command.name)
        if not command.completion.done():
            command.completion.set_result(None)

    async def _handle_shutdown(self, command: _Shutdown) -> None:
        for name in list(self._servers):
            await self._close_server(name)
        if not command.completion.done():
            command.completion.set_result(None)

    async def _close_server(self, name: str) -> None:
        entry = self._servers.pop(name, None)
        if entry is None:
            return
        try:
            await entry.stack.aclose()
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
            env=_build_server_env(cfg),
        )
        read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        listed = await session.list_tools()
        tools: list[McpTool] = []
        for mcp_tool in listed.tools:
            tool_name = f"{server_name}__{mcp_tool.name}"
            description = mcp_tool.description or f"{mcp_tool.name} (from {server_name})"
            schema = dict(mcp_tool.inputSchema)
            input_model = _make_input_model(schema)
            required = tuple(schema.get("required", ()))
            tools.append(
                McpTool(
                    name=tool_name,
                    description=description,
                    input_model=input_model,
                    session=session,
                    mcp_name=mcp_tool.name,
                    required=required,
                )
            )

        logger.info("connected to MCP server %r: %d tool(s)", server_name, len(tools))
        return tools

    async def __aexit__(self, *args: Any) -> None:
        supervisor = self._supervisor
        if supervisor is None:
            return
        complete: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._commands.put_nowait(_Shutdown(complete))
        await complete
        await supervisor
        self._supervisor = None
