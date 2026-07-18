from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Self, override

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


class _BlockingExitContext(_TaskBoundContext):
    """Task-bound context whose cleanup can be held open by a test."""

    def __init__(self) -> None:
        super().__init__()
        self.exit_started = asyncio.Event()
        self.release_exit = asyncio.Event()

    @override
    async def __aexit__(self, *args: object) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.exited_by = task
        if task is not self.entered_by:
            raise RuntimeError("context exited from a different task")
        self.exit_started.set()
        await self.release_exit.wait()


@pytest.mark.asyncio
async def test_connect_many_starts_servers_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    both_started = asyncio.Event()
    started: set[str] = set()

    async def fake_connect(
        name: str,
        cfg: McpServerConfig,
        stack: contextlib.AsyncExitStack,
    ) -> list[object]:
        del cfg, stack
        started.add(name)
        if len(started) == 2:
            both_started.set()
        await both_started.wait()
        return []

    async with McpClientManager() as manager:
        monkeypatch.setattr(manager, "_connect", fake_connect)

        await asyncio.wait_for(
            manager.connect_many(
                {
                    "first": McpServerConfig(command="fake"),
                    "second": McpServerConfig(command="fake"),
                }
            ),
            timeout=0.1,
        )

    assert started == {"first", "second"}


@pytest.mark.asyncio
async def test_hanging_server_does_not_delay_healthy_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_hanging = asyncio.Event()
    healthy_connected = asyncio.Event()

    async def fake_connect(
        name: str,
        cfg: McpServerConfig,
        stack: contextlib.AsyncExitStack,
    ) -> list[object]:
        del cfg, stack
        if name == "hanging":
            await release_hanging.wait()
        else:
            healthy_connected.set()
        return []

    async with McpClientManager() as manager:
        monkeypatch.setattr(manager, "_connect", fake_connect)
        connecting = asyncio.create_task(
            manager.connect_many(
                {
                    "hanging": McpServerConfig(command="fake"),
                    "healthy": McpServerConfig(command="fake"),
                }
            )
        )

        await asyncio.wait_for(healthy_connected.wait(), timeout=0.1)
        assert manager.running_servers == frozenset({"healthy"})

        release_hanging.set()
        await connecting

    assert manager.running_servers == frozenset()


@pytest.mark.asyncio
async def test_connect_many_logs_failed_server_and_keeps_healthy_server(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fake_connect(
        name: str,
        cfg: McpServerConfig,
        stack: contextlib.AsyncExitStack,
    ) -> list[object]:
        del cfg, stack
        if name == "broken":
            raise ConnectionError("unreachable")
        return []

    async with McpClientManager() as manager:
        monkeypatch.setattr(manager, "_connect", fake_connect)

        with caplog.at_level(logging.WARNING):
            await manager.connect_many(
                {
                    "broken": McpServerConfig(command="fake"),
                    "healthy": McpServerConfig(command="fake"),
                }
            )

        assert manager.running_servers == frozenset({"healthy"})
        assert "failed to connect to MCP server 'broken'; skipping" in caplog.text


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
async def test_cancelling_disconnect_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _BlockingExitContext()

    async def fake_connect(
        name: str,
        cfg: McpServerConfig,
        stack: contextlib.AsyncExitStack,
    ) -> list[object]:
        del name, cfg
        await stack.enter_async_context(context)
        return []

    manager = McpClientManager()
    await manager.__aenter__()
    monkeypatch.setattr(manager, "_connect", fake_connect)
    await manager.connect_server("server", McpServerConfig(command="fake"))

    try:
        disconnecting = asyncio.create_task(manager.disconnect_server("server"))
        await context.exit_started.wait()
        disconnecting.cancel()

        with pytest.raises(asyncio.CancelledError):
            await disconnecting
    finally:
        context.release_exit.set()
        await manager.__aexit__(None, None, None)

    assert context.entered_by is context.exited_by


@pytest.mark.asyncio
async def test_cancelling_reconnect_does_not_start_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _BlockingExitContext()
    connect_calls = 0

    async def fake_connect(
        name: str,
        cfg: McpServerConfig,
        stack: contextlib.AsyncExitStack,
    ) -> list[object]:
        nonlocal connect_calls
        del name, cfg
        connect_calls += 1
        if connect_calls == 1:
            await stack.enter_async_context(context)
        return []

    manager = McpClientManager()
    await manager.__aenter__()
    monkeypatch.setattr(manager, "_connect", fake_connect)
    await manager.connect_server("server", McpServerConfig(command="fake"))

    try:
        reconnecting = asyncio.create_task(
            manager.connect_server("server", McpServerConfig(command="replacement"))
        )
        await context.exit_started.wait()
        reconnecting.cancel()

        with pytest.raises(asyncio.CancelledError):
            await reconnecting
        assert connect_calls == 1
    finally:
        context.release_exit.set()
        await manager.__aexit__(None, None, None)


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
    context = _TaskBoundContext()
    started = asyncio.Event()

    async def fake_connect(
        name: str,
        cfg: McpServerConfig,
        stack: contextlib.AsyncExitStack,
    ) -> list[object]:
        del name, cfg
        await stack.enter_async_context(context)
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

    assert context.entered_by is context.exited_by
    await asyncio.wait_for(manager.__aexit__(None, None, None), timeout=0.1)


@pytest.mark.asyncio
async def test_shutdown_cancels_connect_and_closes_context_from_owner_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _TaskBoundContext()
    started = asyncio.Event()

    async def fake_connect(
        name: str,
        cfg: McpServerConfig,
        stack: contextlib.AsyncExitStack,
    ) -> list[object]:
        del name, cfg
        await stack.enter_async_context(context)
        started.set()
        await asyncio.Future[None]()
        return []

    manager = McpClientManager()
    await manager.__aenter__()
    monkeypatch.setattr(manager, "_connect", fake_connect)
    connecting = asyncio.create_task(
        manager.connect_server("hanging", McpServerConfig(command="fake"))
    )
    await started.wait()

    await asyncio.wait_for(manager.__aexit__(None, None, None), timeout=0.1)

    with pytest.raises(asyncio.CancelledError):
        await connecting
    assert context.entered_by is context.exited_by


@pytest.mark.asyncio
async def test_repeated_cancellation_waits_for_mcp_owner_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _BlockingExitContext()

    async def fake_connect(
        name: str,
        cfg: McpServerConfig,
        stack: contextlib.AsyncExitStack,
    ) -> list[object]:
        del name, cfg
        await stack.enter_async_context(context)
        return []

    manager = McpClientManager()
    await manager.__aenter__()
    monkeypatch.setattr(manager, "_connect", fake_connect)
    await manager.connect_server("server", McpServerConfig(command="fake"))

    closing = asyncio.create_task(manager.__aexit__(None, None, None))
    await context.exit_started.wait()
    closing.cancel()
    await asyncio.sleep(0)
    assert closing.done() is False
    closing.cancel()
    await asyncio.sleep(0)
    assert closing.done() is False
    context.release_exit.set()

    with pytest.raises(asyncio.CancelledError):
        await closing
    assert context.entered_by is context.exited_by
    assert manager.running_servers == frozenset()


@pytest.mark.asyncio
async def test_cancelling_connect_cleans_up_its_owner_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _TaskBoundContext()
    started = asyncio.Event()

    async def fake_connect(
        name: str,
        cfg: McpServerConfig,
        stack: contextlib.AsyncExitStack,
    ) -> list[object]:
        del name, cfg
        await stack.enter_async_context(context)
        started.set()
        await asyncio.Future[None]()
        return []

    async with McpClientManager() as manager:
        monkeypatch.setattr(manager, "_connect", fake_connect)
        connecting = asyncio.create_task(
            manager.connect_server("hanging", McpServerConfig(command="fake"))
        )
        await started.wait()

        connecting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await connecting

        assert context.entered_by is context.exited_by
        assert manager.running_servers == frozenset()


@pytest.mark.asyncio
async def test_shutdown_rejects_connect_queued_behind_same_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    connect_calls = 0

    async def fake_connect(
        name: str,
        cfg: McpServerConfig,
        stack: contextlib.AsyncExitStack,
    ) -> list[object]:
        nonlocal connect_calls
        del name, cfg, stack
        connect_calls += 1
        started.set()
        await asyncio.Future[None]()
        return []

    manager = McpClientManager()
    await manager.__aenter__()
    monkeypatch.setattr(manager, "_connect", fake_connect)
    first = asyncio.create_task(manager.connect_server("server", McpServerConfig(command="fake")))
    await started.wait()
    queued = asyncio.create_task(manager.connect_server("server", McpServerConfig(command="fake")))
    await asyncio.sleep(0)

    await asyncio.wait_for(manager.__aexit__(None, None, None), timeout=0.1)

    with pytest.raises(asyncio.CancelledError):
        await first
    with pytest.raises(RuntimeError, match="manager is closed"):
        await queued
    assert connect_calls == 1
    assert manager.running_servers == frozenset()
