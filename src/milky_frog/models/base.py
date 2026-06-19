from typing import Protocol

from milky_frog.domain import ModelRequest, ModelResponse


class Model(Protocol):
    """Seam between the Harness and a model provider adapter."""

    async def complete(self, request: ModelRequest) -> ModelResponse: ...
