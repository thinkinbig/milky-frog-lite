from __future__ import annotations

import asyncio

from milky_frog.domain import (
    ModelRequest,
    ModelResponse,
    ReasoningDelta,
    RunCancellation,
    StreamDone,
    TextDelta,
)
from milky_frog.harness.cancellation import is_cancelled
from milky_frog.harness.copies import copy_model_request
from milky_frog.harness.emitter import RunEmitter
from milky_frog.models import Model


class ModelStreamer:
    """Drain a model stream and forward live chunks through the RunEmitter."""

    def __init__(self, model: Model, emitter: RunEmitter) -> None:
        self._model = model
        self._emitter = emitter

    async def consume(
        self,
        run_id: str,
        cancellation: RunCancellation | None,
        request: ModelRequest,
    ) -> ModelResponse:
        """Return the assembled response after forwarding text and reasoning deltas.

        The terminal ``StreamDone`` carries the response the loop needs to decide
        on tool calls and persist a Checkpoint.
        """
        response: ModelResponse | None = None
        observer_request = copy_model_request(request)
        async for chunk in self._model.stream(request):
            if is_cancelled(cancellation):
                raise asyncio.CancelledError
            if isinstance(chunk, TextDelta):
                await self._emitter.on_model_chunk(run_id, observer_request, chunk)
            elif isinstance(chunk, ReasoningDelta):
                await self._emitter.on_model_reasoning(run_id, observer_request, chunk)
            elif isinstance(chunk, StreamDone):
                response = chunk.response
                break
        if response is None:
            raise RuntimeError("model stream ended without a StreamDone chunk")
        return response
