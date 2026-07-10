from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from dataclasses import dataclass
from typing import Any, TypeVar

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


class _McpConnection:
    """Owns one MCP server's task-affine transport, session, and tools."""

    def __init__(self, tools: list[McpTool], resources: contextlib.AsyncExitStack) -> None:
        self.tools = tools
        self._resources = resources

    async def close(self) -> None:
        """Close the session and transport in reverse creation order."""
        await self._resources.aclose()


@dataclass(frozen=True, slots=True)
class _ConnectServer:
    name: str
    cfg: McpServerConfig


@dataclass(frozen=True, slots=True)
class _DisconnectServer:
    name: str


@dataclass(frozen=True, slots=True)
class _Shutdown:
    pass


type _McpCommand = _ConnectServer | _DisconnectServer | _Shutdown
_ResultT = TypeVar("_ResultT")


@dataclass(frozen=True, slots=True)
class _QueuedCommand:
    command: _McpCommand
    completion: asyncio.Future[Any]


class McpClientManager:
    """Manages per-server MCP connections with independent lifecycles.

    A single supervisor Task owns every stdio context. This ensures anyio
    cancel scopes created by ``stdio_client()`` are exited by the same Task that
    entered them, even when a reload or shutdown originates in another Task.
    """

    def __init__(self, *, connect_timeout: float = 30.0) -> None:
        self._servers: dict[str, _McpConnection] = {}
        self._commands: asyncio.Queue[_QueuedCommand] = asyncio.Queue()
        self._supervisor: asyncio.Task[None] | None = None
        self._connect_timeout = connect_timeout

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
        return await self._submit(_ConnectServer(name, cfg))

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
        await self._submit(_DisconnectServer(name))

    async def _submit(self, command: _McpCommand) -> _ResultT:
        self._ensure_supervisor()
        completion: asyncio.Future[_ResultT] = asyncio.get_running_loop().create_future()
        self._commands.put_nowait(_QueuedCommand(command, completion))
        return await completion

    def _ensure_supervisor(self) -> None:
        if self._supervisor is None:
            self._supervisor = asyncio.create_task(self._serve())

    async def _serve(self) -> None:
        """Process lifecycle changes in the Task that owns all stdio contexts."""
        while True:
            queued = await self._commands.get()
            try:
                result = await self._execute(queued.command)
            except Exception as error:
                if not queued.completion.done():
                    queued.completion.set_exception(error)
            else:
                if not queued.completion.done():
                    queued.completion.set_result(result)
            if isinstance(queued.command, _Shutdown):
                return

    async def _execute(self, command: _McpCommand) -> Any:
        match command:
            case _ConnectServer(name, cfg):
                return await self._connect_server(name, cfg)
            case _DisconnectServer(name):
                return await self._disconnect_server(name)
            case _Shutdown():
                return await self._shutdown()

    async def _connect_server(self, name: str, cfg: McpServerConfig) -> list[McpTool]:
        await self._close_server(name)
        connection = await self._open_connection(name, cfg)
        self._servers[name] = connection
        return connection.tools

    async def _open_connection(self, name: str, cfg: McpServerConfig) -> _McpConnection:
        stack = contextlib.AsyncExitStack()
        try:
            async with asyncio.timeout(self._connect_timeout):
                await stack.__aenter__()
                tools = await self._connect(name, cfg, stack)
        except Exception:
            try:
                await stack.aclose()
            except Exception:
                logger.warning(
                    "error closing MCP server %r after connection failure",
                    name,
                    exc_info=True,
                )
            raise
        return _McpConnection(tools, stack)

    async def _disconnect_server(self, name: str) -> None:
        await self._close_server(name)

    async def _shutdown(self) -> None:
        for name in list(self._servers):
            await self._close_server(name)

    async def _close_server(self, name: str) -> None:
        entry = self._servers.pop(name, None)
        if entry is None:
            return
        try:
            await entry.close()
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
        self._commands.put_nowait(_QueuedCommand(_Shutdown(), complete))
        await complete
        await supervisor
        self._supervisor = None
