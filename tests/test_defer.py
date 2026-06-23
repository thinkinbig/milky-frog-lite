from __future__ import annotations

import asyncio
import logging

import pytest

from milky_frog.defer import DeferStack


def test_defer_runs_callbacks_in_lifo_order() -> None:
    order: list[str] = []
    stack = DeferStack()
    stack.defer(order.append, "first")
    stack.defer(order.append, "second")
    stack.defer(order.append, "third")
    stack.run()
    assert order == ["third", "second", "first"]


def test_defer_isolates_failures_when_logger_is_set(caplog: pytest.LogCaptureFixture) -> None:
    order: list[str] = []
    stack = DeferStack(logger=logging.getLogger("test.defer"))

    def boom() -> None:
        raise RuntimeError("boom")

    stack.defer(order.append, "after")
    stack.defer(boom, label="boom")
    stack.defer(order.append, "before")

    with caplog.at_level(logging.ERROR):
        stack.run()

    assert order == ["before", "after"]
    assert "Defer failed: boom" in caplog.text


def test_defer_run_sync_awaits_coroutines() -> None:
    order: list[str] = []

    async def async_cleanup() -> None:
        order.append("async")

    loop = asyncio.new_event_loop()
    try:
        stack = DeferStack()
        stack.defer(async_cleanup)
        stack.defer(order.append, "sync")
        stack.run_sync(loop)
    finally:
        loop.close()

    assert order == ["sync", "async"]


@pytest.mark.asyncio
async def test_defer_run_async_awaits_coroutines() -> None:
    order: list[str] = []

    async def async_cleanup() -> None:
        order.append("async")

    stack = DeferStack()
    stack.defer(async_cleanup)
    stack.defer(order.append, "sync")
    await stack.run_async()
    assert order == ["sync", "async"]


def test_defer_context_manager_runs_on_exit() -> None:
    order: list[str] = []
    with DeferStack() as stack:
        stack.defer(order.append, "done")
    assert order == ["done"]


def test_defer_set_runs_on_exit() -> None:
    class Holder:
        value: str = "before"

    holder = Holder()
    with DeferStack() as stack:
        stack.defer_set(holder, "value", "after")
    assert holder.value == "after"


def test_defer_aclose_awaits_resource() -> None:
    closed: list[str] = []

    class Resource:
        async def aclose(self) -> None:
            closed.append("done")

    loop = asyncio.new_event_loop()
    try:
        stack = DeferStack()
        stack.defer_aclose(Resource())
        stack.run_sync(loop)
    finally:
        loop.close()
    assert closed == ["done"]


def test_defer_sync_on_runs_on_exit_and_awaits_async() -> None:
    order: list[str] = []

    async def async_cleanup() -> None:
        order.append("async")

    loop = asyncio.new_event_loop()
    try:
        with DeferStack().sync_on(loop) as stack:
            stack.defer(async_cleanup)
            stack.defer(order.append, "sync")
    finally:
        loop.close()

    assert order == ["sync", "async"]


def test_defer_run_rejects_awaitable_without_loop() -> None:
    async def async_cleanup() -> None:
        return None

    def returns_coro() -> object:
        return async_cleanup()

    stack = DeferStack()
    stack.defer(returns_coro)
    with pytest.raises(TypeError, match="run_sync"):
        stack.run()
