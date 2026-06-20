from __future__ import annotations

from pathlib import Path
from typing import cast
from uuid import uuid4

from pydantic import JsonValue

from milky_frog.checkpoint import CheckpointStore, RunEvent
from milky_frog.domain import (
    Message,
    MessageRole,
    ModelRequest,
    RunRequest,
    RunResult,
    RunStatus,
    ToolCall,
)
from milky_frog.handlers import (
    AfterModel,
    AfterTool,
    BeforeModel,
    BeforeTool,
    HandlerRegistry,
    RunFailed,
)
from milky_frog.harness.prompt import system_prompt
from milky_frog.models import Model
from milky_frog.harness.tools import ToolContext, ToolRegistry, ToolResult


class Harness:
    """Advances one durable Run through a linear model and Tool loop."""

    def __init__(
        self,
        model: Model,
        tools: ToolRegistry,
        checkpoints: CheckpointStore,
        handlers: HandlerRegistry,
    ) -> None:
        self._model = model
        self._tools = tools
        self._checkpoints = checkpoints
        self._handlers = handlers

    async def run(self, run_request: RunRequest) -> RunResult:
        run_id = uuid4().hex
        workspace = run_request.workspace.resolve(strict=True)
        self._checkpoints.create_run(run_id, workspace)
        self._checkpoints.append(
            run_id,
            RunEvent(
                "RunStarted",
                {"prompt": run_request.prompt, "workspace": str(workspace)},
            ),
        )
        messages = [
            Message(MessageRole.SYSTEM, system_prompt(workspace)),
            Message(MessageRole.USER, run_request.prompt),
        ]

        try:
            for model_call in range(1, run_request.max_model_calls + 1):
                request = ModelRequest(tuple(messages), self._tools.schemas())
                await self._handlers.dispatch(BeforeModel(run_id, request))
                response = await self._model.complete(request)
                await self._handlers.dispatch(AfterModel(run_id, request, response))
                self._checkpoints.append(
                    run_id,
                    RunEvent(
                        "ModelMessageCompleted",
                        {
                            "content": response.content,
                            "tool_calls": [
                                {
                                    "id": call.id,
                                    "name": call.name,
                                    "arguments": call.arguments,
                                }
                                for call in response.tool_calls
                            ],
                            "usage": cast(JsonValue, response.usage),
                        },
                    ),
                )
                messages.append(
                    Message(MessageRole.ASSISTANT, response.content, response.tool_calls)
                )

                if not response.tool_calls:
                    self._checkpoints.append(
                        run_id,
                        RunEvent("RunCompleted", {"final_message": response.content}),
                        RunStatus.COMPLETED,
                    )
                    return RunResult(run_id, RunStatus.COMPLETED, response.content, model_call)

                for call in response.tool_calls:
                    result = await self._execute_tool(run_id, workspace, call)
                    messages.append(
                        Message(
                            MessageRole.TOOL,
                            result.content,
                            tool_call_id=call.id,
                        )
                    )

            message = f"model call limit reached ({run_request.max_model_calls})"
            self._checkpoints.append(
                run_id,
                RunEvent("RunPaused", {"reason": message}),
                RunStatus.PAUSED_LIMIT,
            )
            return RunResult(
                run_id,
                RunStatus.PAUSED_LIMIT,
                message,
                run_request.max_model_calls,
            )
        except Exception as error:
            await self._handlers.dispatch(RunFailed(run_id, error))
            self._checkpoints.append(
                run_id,
                RunEvent("RunFailed", {"error_type": type(error).__name__, "message": str(error)}),
                RunStatus.FAILED,
            )
            raise

    async def _execute_tool(self, run_id: str, workspace: Path, call: ToolCall) -> ToolResult:
        await self._handlers.dispatch(BeforeTool(run_id, call))
        self._checkpoints.append(
            run_id,
            RunEvent(
                "ToolCallRequested",
                {"id": call.id, "name": call.name, "arguments": call.arguments},
            ),
        )
        tool = self._tools.get(call.name)
        input_model = tool.input_model.model_validate(call.arguments)
        try:
            result = await tool.execute(ToolContext(run_id, workspace), input_model)
        except Exception as error:
            result = ToolResult(f"{type(error).__name__}: {error}", is_error=True)
        await self._handlers.dispatch(AfterTool(run_id, call, result))
        self._checkpoints.append(
            run_id,
            RunEvent(
                "ToolCallCompleted",
                {
                    "id": call.id,
                    "name": call.name,
                    "content": result.content,
                    "is_error": result.is_error,
                },
            ),
        )
        return result
