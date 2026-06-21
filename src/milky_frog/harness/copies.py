from __future__ import annotations

from copy import deepcopy

from milky_frog.domain import (
    ModelRequest,
    ModelResponse,
    RunCancellation,
    RunRequest,
    ToolCall,
)
from milky_frog.harness.steering import DetachedSteeringChannel


def copy_tool_call(call: ToolCall) -> ToolCall:
    return ToolCall(call.id, call.name, deepcopy(call.arguments))


def copy_model_request(request: ModelRequest) -> ModelRequest:
    return deepcopy(request)


def copy_model_response(response: ModelResponse) -> ModelResponse:
    return deepcopy(response)


def copy_run_request(request: RunRequest) -> RunRequest:
    cancellation = None
    if request.cancellation is not None:
        cancellation = RunCancellation()
        if request.cancellation.is_cancelled:
            cancellation.cancel()
    steering = DetachedSteeringChannel() if request.steering is not None else None
    return RunRequest(
        prompt=request.prompt,
        workspace=request.workspace,
        max_model_calls=request.max_model_calls,
        cancellation=cancellation,
        steering=steering,
    )
