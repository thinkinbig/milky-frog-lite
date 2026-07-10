from __future__ import annotations

import asyncio
import contextlib
from typing import Self

import pytest

from milky_frog.harness.mcp.client import McpClientManager
from milky_frog.harness.mcp.config import McpServerConfig


class _TaskBoundContext:
    """Async context that rejects cleanup from a task other than its owner."""

    def __init__(self) -> None:
        self.entered_by: asyncio.Task[object] | None = None
        self.exited_by: asyncio.Task[object] | None = None

    async def __aenter__(self) -> Self:
        task = asyncio.current_task()
        assert task is not None
        self.entered_by = task
        return self

    async def __aexit__(self, *args: object) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.exited_by = task
        if task is not self.entered_by:
            raise RuntimeError("context exited from a different task")


@pytest.mark.asyncio
async def test_disconnect_closes_stdio_context_from_its_connection_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _TaskBoundContext()

    async def fake_connect(
        name: str,
        cfg: McpServerConfig,
        stack: contextlib.AsyncExitStack,
    ) -> list[object]:
        del name, cfg
        await stack.enter_async_context(context)
        return []

    async with McpClientManager() as manager:
        monkeypatch.setattr(manager, "_connect", fake_connect)

        caller = asyncio.current_task()
        assert caller is not None
        await manager.connect_many({"server": McpServerConfig(command="fake")})
        assert context.entered_by is not caller
        await manager.disconnect_server("server")

    assert context.entered_by is context.exited_by


@pytest.mark.asyncio
async def test_failed_connection_closes_stdio_context_from_its_connection_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _TaskBoundContext()

    async def fake_connect(
        name: str,
        cfg: McpServerConfig,
        stack: contextlib.AsyncExitStack,
    ) -> list[object]:
        del name, cfg
        await stack.enter_async_context(context)
        raise ConnectionError("cannot initialize")

    async with McpClientManager() as manager:
        monkeypatch.setattr(manager, "_connect", fake_connect)

        with pytest.raises(ConnectionError, match="cannot initialize"):
            await manager.connect_server("server", McpServerConfig(command="fake"))

    assert context.entered_by is context.exited_by


@pytest.mark.asyncio
async def test_hanging_connection_times_out_and_does_not_block_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()

    async def fake_connect(
        name: str,
        cfg: McpServerConfig,
        stack: contextlib.AsyncExitStack,
    ) -> list[object]:
        del name, cfg, stack
        started.set()
        await asyncio.Future[None]()
        return []

    manager = McpClientManager(connect_timeout=0.01)
    await manager.__aenter__()
    monkeypatch.setattr(manager, "_connect", fake_connect)

    connecting = asyncio.create_task(
        manager.connect_server("hanging", McpServerConfig(command="fake"))
    )
    await started.wait()

    with pytest.raises(asyncio.TimeoutError):
        await connecting

    await asyncio.wait_for(manager.__aexit__(None, None, None), timeout=0.1)
