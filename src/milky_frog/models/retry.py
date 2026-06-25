from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Coroutine
from typing import Any

from milky_frog.domain import ModelChunk, ModelRequest
from milky_frog.models.base import Model


def is_retriable_model_error(error: BaseException) -> bool:
    if isinstance(error, (ConnectionError, TimeoutError, OSError)):
        return True
    try:
        from openai import APIConnectionError, APITimeoutError
    except ImportError:
        return False
    return isinstance(error, (APIConnectionError, APITimeoutError))


class RetryingModel:
    """Wraps any Model with transparent retry on transient connection failures."""

    def __init__(
        self,
        inner: Model,
        notify: Callable[[str, str], Coroutine[Any, Any, Any]],
        *,
        max_attempts: int = 3,
        base_delay: float = 1.0,
    ) -> None:
        self._inner = inner
        self._notify = notify
        self._max_attempts = max_attempts
        self._base_delay = base_delay

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk, None]:
        for attempt in range(1, self._max_attempts + 1):
            try:
                async for chunk in self._inner.stream(request):
                    yield chunk
                return
            except Exception as error:
                if not is_retriable_model_error(error) or attempt >= self._max_attempts:
                    raise
                await self._notify(
                    request.run_id,
                    f"Cannot reach model ({type(error).__name__}) — "
                    f"retrying ({attempt + 1}/{self._max_attempts})",
                )
                await asyncio.sleep(self._base_delay * attempt)
