from __future__ import annotations

import asyncio

MODEL_RETRY_MAX_ATTEMPTS = 3
MODEL_RETRY_BASE_DELAY_S = 1.0


def is_retriable_model_error(error: BaseException) -> bool:
    """Return whether a model-provider failure is worth retrying."""
    if isinstance(error, (ConnectionError, TimeoutError, OSError)):
        return True
    try:
        from openai import APIConnectionError, APITimeoutError
    except ImportError:
        return False
    return isinstance(error, (APIConnectionError, APITimeoutError))


async def retry_sleep(delay_s: float) -> None:
    """Pause between model retries. Extracted for tests."""
    await asyncio.sleep(delay_s)
