from collections.abc import AsyncGenerator
from typing import Protocol

from milky_frog.domain import ModelChunk, ModelRequest


class Model(Protocol):
    """Seam between the Harness and a model provider adapter.

    A Model streams a Run forward: it yields ``TextDelta`` fragments as the
    provider produces them, then exactly one ``StreamDone`` carrying the
    assembled ``ModelResponse`` (content, tool calls, usage).
    """

    def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk, None]: ...
