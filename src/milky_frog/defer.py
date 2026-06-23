from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol, Self


class _HasClose(Protocol):
    def close(self) -> object: ...


class _HasAclose(Protocol):
    def aclose(self) -> Awaitable[object]: ...


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _Deferred:
    invoke: Callable[[], object]
    label: str


class DeferStack:
    """Register cleanups to run LIFO on exit, like Go ``defer``.

    Each callback runs in isolation: one failure is logged and the rest still
    run. Callbacks may return ``None`` or an awaitable; use ``sync_on(loop)``,
    ``run_sync(loop)``, or ``await run_async()`` when any callback is async.

    Prefer the named ``defer_*`` helpers over raw callbacks at call sites.
    Use ``with stack`` only for sync-only cleanups; ``with stack.sync_on(loop)``
    when ``defer_aclose`` or other async callbacks are registered.
    """

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self._callbacks: list[_Deferred] = []
        self._logger = logger

    def defer(
        self,
        callback: Callable[..., object],
        /,
        *args: Any,
        label: str | None = None,
        **kwargs: Any,
    ) -> Self:
        """Schedule *callback* to run on ``run*``; last deferred runs first."""
        if label is not None:
            name = label
        else:
            qualname = getattr(callback, "__qualname__", None)
            name = qualname if isinstance(qualname, str) and qualname else repr(callback)

        def invoke() -> object:
            return callback(*args, **kwargs)

        self._callbacks.append(_Deferred(invoke, name))
        return self

    def defer_set(self, obj: object, name: str, value: object) -> Self:
        """Defer ``setattr(obj, name, value)``."""
        return self.defer(setattr, obj, name, value, label=f"set {name}")

    def defer_close(self, resource: _HasClose, *, label: str | None = None) -> Self:
        """Defer ``resource.close()``."""
        return self.defer(resource.close, label=label or f"{type(resource).__name__}.close")

    def defer_aclose(self, resource: _HasAclose, *, label: str | None = None) -> Self:
        """Defer ``resource.aclose()`` (async cleanup protocol)."""
        return self.defer(resource.aclose, label=label or type(resource).__name__)

    def defer_shutdown_asyncgens(self, loop: asyncio.AbstractEventLoop) -> Self:
        """Defer ``loop.shutdown_asyncgens()``."""
        return self.defer(loop.shutdown_asyncgens, label="shutdown_asyncgens")

    def defer_yield_loop(self, loop: asyncio.AbstractEventLoop) -> Self:
        """Defer one no-op turn so pending loop callbacks can finish."""
        return self.defer(lambda: asyncio.sleep(0), label="yield_loop")

    def defer_signal(
        self,
        signum: int,
        handler: signal.Handlers,
        *,
        label: str | None = None,
    ) -> Self:
        """Defer restoring a POSIX signal handler."""
        return self.defer(signal.signal, signum, handler, label=label or f"restore signal {signum}")

    def run(self) -> None:
        """Run deferred sync callbacks (LIFO)."""
        while self._callbacks:
            deferred = self._callbacks.pop()
            try:
                result = deferred.invoke()
            except Exception:
                if self._logger is not None:
                    self._logger.exception("Defer failed: %s", deferred.label)
                else:
                    raise
                continue
            if isinstance(result, Awaitable):
                if asyncio.iscoroutine(result):
                    result.close()
                msg = f"async defer callback requires run_sync() or run_async(): {deferred.label}"
                raise TypeError(msg)

    def run_sync(self, loop: asyncio.AbstractEventLoop) -> None:
        """Run deferred callbacks, awaiting any awaitables on *loop*."""
        while self._callbacks:
            deferred = self._callbacks.pop()
            try:
                result = deferred.invoke()
                if isinstance(result, Awaitable):
                    loop.run_until_complete(result)
            except Exception:
                if self._logger is not None:
                    self._logger.exception("Defer failed: %s", deferred.label)
                else:
                    raise

    async def run_async(self) -> None:
        """Run deferred callbacks, awaiting any awaitables on the current loop."""
        while self._callbacks:
            deferred = self._callbacks.pop()
            try:
                result = deferred.invoke()
                if isinstance(result, Awaitable):
                    await result
            except Exception:
                if self._logger is not None:
                    self._logger.exception("Defer failed: %s", deferred.label)
                else:
                    raise

    @contextmanager
    def sync_on(self, loop: asyncio.AbstractEventLoop) -> Iterator[Self]:
        """Defer cleanups until exit, then run them via ``run_sync(loop)``."""
        try:
            yield self
        finally:
            self.run_sync(loop)

    def __enter__(self) -> DeferStack:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.run()
