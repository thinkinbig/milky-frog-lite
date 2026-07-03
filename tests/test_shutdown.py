from __future__ import annotations

from dataclasses import dataclass, field
from types import TracebackType

import pytest

from milky_frog.core.shutdown import ShutdownManager


@dataclass
class _FakeForeground:
    shutdown_calls: int = 0

    def shutdown(self) -> None:
        self.shutdown_calls += 1


@dataclass
class _FakeHandler:
    name: str
    exit_calls: list[str] = field(default_factory=list)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.exit_calls.append(self.name)


class _FakeModel:
    exit_calls: int = 0

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.exit_calls += 1


@pytest.mark.asyncio
async def test_shutdown_run_is_idempotent() -> None:
    fg = _FakeForeground()
    mgr = ShutdownManager()
    mgr.wire(fg, [], _FakeModel())  # type: ignore[arg-type]

    mgr.shutdown_run()
    mgr.shutdown_run()
    mgr.shutdown_run()

    assert fg.shutdown_calls == 1


@pytest.mark.asyncio
async def test_shutdown_request_before_wire_is_applied_when_wired() -> None:
    fg = _FakeForeground()
    mgr = ShutdownManager()

    mgr.request_shutdown()
    mgr.wire(fg, [], _FakeModel())  # type: ignore[arg-type]
    mgr.shutdown_run()

    assert mgr.requested is True
    assert fg.shutdown_calls == 1


@pytest.mark.asyncio
async def test_shutdown_request_cancels_worker_attached_late() -> None:
    cancel_calls = 0
    mgr = ShutdownManager()

    def cancel() -> None:
        nonlocal cancel_calls
        cancel_calls += 1

    mgr.request_shutdown()
    mgr.attach_worker(cancel)
    mgr.wire(_FakeForeground(), [], _FakeModel())  # type: ignore[arg-type]

    assert cancel_calls == 1


@pytest.mark.asyncio
async def test_cleanup_is_idempotent() -> None:
    model = _FakeModel()
    mgr = ShutdownManager()
    mgr.wire(_FakeForeground(), [], model)  # type: ignore[arg-type]

    await mgr.cleanup(None, None, None)
    await mgr.cleanup(None, None, None)

    assert model.exit_calls == 1


@pytest.mark.asyncio
async def test_cleanup_shuts_down_run_first() -> None:
    fg = _FakeForeground()
    mgr = ShutdownManager()
    mgr.wire(fg, [], _FakeModel())  # type: ignore[arg-type]

    await mgr.cleanup(None, None, None)

    assert fg.shutdown_calls == 1


@pytest.mark.asyncio
async def test_shutdown_run_cancels_worker() -> None:
    cancelled = False

    def cancel() -> None:
        nonlocal cancelled
        cancelled = True

    mgr = ShutdownManager()
    mgr.wire(_FakeForeground(), [], _FakeModel())  # type: ignore[arg-type]
    mgr.attach_worker(cancel)

    mgr.shutdown_run()

    assert cancelled is True


@pytest.mark.asyncio
async def test_releases_handlers_in_reverse_order() -> None:
    handler_a = _FakeHandler("a")
    handler_b = _FakeHandler("b")
    handler_c = _FakeHandler("c")

    mgr = ShutdownManager()
    mgr.wire(
        _FakeForeground(),  # type: ignore[arg-type]
        [handler_a, handler_b, handler_c],  # type: ignore[list-item]
        _FakeModel(),  # type: ignore[arg-type]
    )

    await mgr.cleanup(None, None, None)

    assert handler_c.exit_calls == ["c"]
    assert handler_b.exit_calls == ["b"]
    assert handler_a.exit_calls == ["a"]


@pytest.mark.asyncio
async def test_failing_handler_does_not_block_siblings() -> None:
    class _BoomHandler:
        async def __aexit__(self, *args: object) -> None:
            raise RuntimeError("boom")

    ok = _FakeHandler("ok")

    mgr = ShutdownManager()
    mgr.wire(
        _FakeForeground(),  # type: ignore[arg-type]
        [_BoomHandler(), ok],  # type: ignore[list-item]
        _FakeModel(),  # type: ignore[arg-type]
    )

    # Must not raise, and the second handler must still run.
    await mgr.cleanup(None, None, None)

    assert ok.exit_calls == ["ok"]
