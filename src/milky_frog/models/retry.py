from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Coroutine
from typing import Any

from milky_frog.domain import ModelChunk, ModelRequest
from milky_frog.models.base import Model

_MAX_ATTEMPTS = 3
_BASE_DELAY_S = 1.0


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
    ) -> None:
        self._inner = inner
        self._notify = notify

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk, None]:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                async for chunk in self._inner.stream(request):
                    yield chunk
                return
            except Exception as error:
                if not is_retriable_model_error(error) or attempt >= _MAX_ATTEMPTS:
                    raise
                await self._notify(
                    request.run_id,
                    f"Cannot reach model ({type(error).__name__}) — "
                    f"retrying ({attempt + 1}/{_MAX_ATTEMPTS})",
                )
                await asyncio.sleep(_BASE_DELAY_S * attempt)
