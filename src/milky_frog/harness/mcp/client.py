from __future__ import annotations

import asyncio
import contextlib
import logging
import os
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


class _McpConnection:
    """Owns one MCP server's task-affine transport, session, and tools."""

    def __init__(self, tools: list[McpTool], resources: contextlib.AsyncExitStack) -> None:
        self.tools = tools
        self._resources = resources

    async def close(self) -> None:
        """Close the session and transport in reverse creation order."""
        await self._resources.aclose()


class _McpServerOwner:
    """Keeps one server's connection resources in their owning Task."""

    def __init__(self) -> None:
        loop = asyncio.get_running_loop()
        self.ready: asyncio.Future[list[McpTool]] = loop.create_future()
        self.stop = asyncio.Event()
        self.connection: _McpConnection | None = None
        self.task: asyncio.Task[None] | None = None


class McpClientManager:
    """Manages per-server MCP connections with independent lifecycles.

    Each server has an owner Task that opens, serves, and closes its stdio
    contexts. This preserves anyio cancel-scope task affinity while allowing
    independent servers to connect concurrently.
    """

    def __init__(self, *, connect_timeout: float = 30.0) -> None:
        self._servers: dict[str, _McpConnection] = {}
        self._owners: dict[str, _McpServerOwner] = {}
        self._server_order: list[str] = []
        self._server_locks: dict[str, asyncio.Lock] = {}
        self._lifecycle_lock = asyncio.Lock()
        self._connect_timeout = connect_timeout
        self._closed = False

    @property
    def running_servers(self) -> frozenset[str]:
        return frozenset(self._servers)

    @property
    def tools(self) -> list[McpTool]:
        return [
            tool
            for name in self._server_order
            if (server := self._servers.get(name)) is not None
            for tool in server.tools
        ]

    async def __aenter__(self) -> McpClientManager:
        async with self._lifecycle_lock:
            self._closed = False
        return self

    async def connect_server(self, name: str, cfg: McpServerConfig) -> list[McpTool]:
        """Connect one server through a Task that owns its stdio contexts."""
        lock = self._server_locks.setdefault(name, asyncio.Lock())
        async with lock:
            await self._stop_server(name)
            async with self._lifecycle_lock:
                if self._closed:
                    raise RuntimeError("MCP client manager is closed")
                owner = _McpServerOwner()
                self._owners[name] = owner
                self._server_order.append(name)
                owner.task = asyncio.create_task(self._own_server(name, cfg, owner))
            try:
                return await asyncio.shield(owner.ready)
            except asyncio.CancelledError:
                await self._stop_server(name)
                raise

    async def connect_many(self, servers: dict[str, McpServerConfig]) -> None:
        """Connect several independent servers without one failure blocking the rest.

        Each server retains its own timeout and failure handling, while the
        connections overlap so startup is bounded by roughly one timeout.
        """

        async def _connect_one(name: str, cfg: McpServerConfig) -> None:
            try:
                await self.connect_server(name, cfg)
            except Exception:
                logger.warning("failed to connect to MCP server %r; skipping", name, exc_info=True)

        await asyncio.gather(*(_connect_one(name, cfg) for name, cfg in servers.items()))

    async def disconnect_server(self, name: str) -> None:
        """Disconnect one server and remove its tools."""
        lock = self._server_locks.setdefault(name, asyncio.Lock())
        async with lock:
            await self._stop_server(name)

    async def _own_server(
        self,
        name: str,
        cfg: McpServerConfig,
        owner: _McpServerOwner,
    ) -> None:
        """Open and close one connection entirely within its owner Task."""
        try:
            connection = await self._open_connection(name, cfg)
        except asyncio.CancelledError:
            self._forget_owner(name, owner)
            if not owner.ready.done():
                owner.ready.cancel()
            return
        except Exception as error:
            self._forget_owner(name, owner)
            if not owner.ready.done():
                owner.ready.set_exception(error)
            return

        owner.connection = connection
        if self._owners.get(name) is owner:
            self._servers[name] = connection
        if not owner.ready.done():
            owner.ready.set_result(connection.tools)

        try:
            await owner.stop.wait()
        finally:
            if self._servers.get(name) is connection:
                self._servers.pop(name)
            try:
                await connection.close()
            except Exception:
                logger.warning("error closing MCP server %r", name, exc_info=True)
            finally:
                self._forget_owner(name, owner)

    async def _stop_server(self, name: str) -> None:
        owner = self._owners.get(name)
        if owner is None:
            return

        task = owner.task
        if task is None:
            self._forget_owner(name, owner)
            return
        cancelled_by_manager = owner.connection is None
        if cancelled_by_manager:
            task.cancel()
        else:
            owner.stop.set()
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            caller = asyncio.current_task()
            caller_is_cancelled = caller is not None and caller.cancelling() > 0
            expected_owner_cancel = (
                cancelled_by_manager and task.cancelled() and not caller_is_cancelled
            )
            if not expected_owner_cancel:
                raise

    def _forget_owner(self, name: str, owner: _McpServerOwner) -> None:
        if self._owners.get(name) is not owner:
            return
        self._owners.pop(name)
        connection = owner.connection
        if connection is not None and self._servers.get(name) is connection:
            self._servers.pop(name)
        self._server_order.remove(name)

    async def _open_connection(self, name: str, cfg: McpServerConfig) -> _McpConnection:
        stack = contextlib.AsyncExitStack()
        try:
            async with asyncio.timeout(self._connect_timeout):
                await stack.__aenter__()
                tools = await self._connect(name, cfg, stack)
        except BaseException:
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
        async with self._lifecycle_lock:
            self._closed = True
            await asyncio.gather(*(self._stop_server(name) for name in tuple(self._owners)))
