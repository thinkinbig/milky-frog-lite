from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast
from uuid import uuid4

from pydantic import JsonValue

from milky_frog.checkpoint import CheckpointStore, RunEvent
from milky_frog.domain import (
    Message,
    MessageRole,
    ModelRequest,
    ModelResponse,
    ReasoningDelta,
    RunRequest,
    RunResult,
    RunStatus,
    StreamDone,
    TextDelta,
    ToolCall,
    ToolResult,
)
from milky_frog.handlers import (
    AfterModel,
    AfterTool,
    BeforeModel,
    BeforeTool,
    BlockTool,
    HandlerRegistry,
    OnModelChunk,
    OnModelReasoning,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunPaused,
    RunStarted,
)
from milky_frog.harness.prompt import system_prompt
from milky_frog.harness.tools import ToolContext, ToolRegistry
from milky_frog.models import Model


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
        model_calls = 0
        self._checkpoints.create_run(run_id, workspace)
        self._checkpoints.append(
            run_id,
            RunEvent(
                "RunStarted",
                {"prompt": run_request.prompt, "workspace": str(workspace)},
            ),
        )
        await self._handlers.dispatch(RunStarted(run_id=run_id, request=run_request))
        messages = [
            Message(MessageRole.SYSTEM, system_prompt(workspace)),
            Message(MessageRole.USER, run_request.prompt),
        ]

        try:
            for model_call in range(1, run_request.max_model_calls + 1):
                model_calls = model_call
                if self._should_cancel(run_request):
                    return await self._finish_cancelled(run_id, "cancelled", model_call - 1)
                request = ModelRequest(tuple(messages), self._tools.schemas())
                before_model = BeforeModel(run_id=run_id, request=request)
                await self._handlers.dispatch(before_model)
                response = await self._consume_stream(run_id, run_request, before_model.request)
                await self._handlers.dispatch(
                    AfterModel(
                        run_id=run_id,
                        request=before_model.request,
                        response=response,
                    )
                )
                self._checkpoints.append(
                    run_id,
                    RunEvent(
                        "ModelMessageCompleted",
                        {
                            "content": response.content,
                            "reasoning": response.reasoning,
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
                # Reasoning is intentionally dropped from history: reasoning
                # providers reject their own reasoning_content on input.
                messages.append(
                    Message(MessageRole.ASSISTANT, response.content, response.tool_calls)
                )

                if not response.tool_calls:
                    result = RunResult(run_id, RunStatus.COMPLETED, response.content, model_call)
                    self._checkpoints.append(
                        run_id,
                        RunEvent("RunCompleted", {"final_message": response.content}),
                        RunStatus.COMPLETED,
                    )
                    await self._handlers.dispatch(RunCompleted(run_id=run_id, result=result))
                    return result

                for call in response.tool_calls:
                    if self._should_cancel(run_request):
                        return await self._finish_cancelled(run_id, "cancelled", model_call)
                    tool_result = await self._execute_tool(run_id, workspace, call)
                    messages.append(
                        Message(
                            MessageRole.TOOL,
                            tool_result.content,
                            tool_call_id=call.id,
                        )
                    )

            message = f"model call limit reached ({run_request.max_model_calls})"
            result = RunResult(
                run_id,
                RunStatus.PAUSED_LIMIT,
                message,
                run_request.max_model_calls,
            )
            self._checkpoints.append(
                run_id,
                RunEvent("RunPaused", {"reason": message}),
                RunStatus.PAUSED_LIMIT,
            )
            await self._handlers.dispatch(
                RunPaused(
                    run_id=run_id,
                    status=RunStatus.PAUSED_LIMIT,
                    reason=message,
                    model_calls=run_request.max_model_calls,
                )
            )
            return result
        except asyncio.CancelledError:
            return await self._finish_cancelled(run_id, "cancelled", model_calls)
        except Exception as error:
            await self._handlers.dispatch(RunFailed(run_id=run_id, error=error))
            self._checkpoints.append(
                run_id,
                RunEvent("RunFailed", {"error_type": type(error).__name__, "message": str(error)}),
                RunStatus.FAILED,
            )
            raise

    async def _consume_stream(
        self,
        run_id: str,
        run_request: RunRequest,
        request: ModelRequest,
    ) -> ModelResponse:
        """Drain a model stream, forwarding text deltas and returning the response.

        Text fragments are dispatched as ``OnModelChunk`` so the UI can render
        live; the terminal ``StreamDone`` carries the assembled response the
        loop needs to decide on tool calls and persist a Checkpoint.
        """
        response: ModelResponse | None = None
        async for chunk in self._model.stream(request):
            if self._should_cancel(run_request):
                raise asyncio.CancelledError
            if isinstance(chunk, TextDelta):
                await self._handlers.dispatch(
                    OnModelChunk(run_id=run_id, request=request, chunk=chunk)
                )
            elif isinstance(chunk, ReasoningDelta):
                await self._handlers.dispatch(
                    OnModelReasoning(run_id=run_id, request=request, chunk=chunk)
                )
            elif isinstance(chunk, StreamDone):
                response = chunk.response
                break
        if response is None:
            raise RuntimeError("model stream ended without a StreamDone chunk")
        return response

    async def _execute_tool(
        self,
        run_id: str,
        workspace: Path,
        call: ToolCall,
    ) -> ToolResult:
        block = await self._handlers.dispatch(BeforeTool(run_id=run_id, call=call))
        self._checkpoints.append(
            run_id,
            RunEvent(
                "ToolCallRequested",
                {"id": call.id, "name": call.name, "arguments": call.arguments},
            ),
        )
        if isinstance(block, BlockTool):
            result = ToolResult(block.reason, is_error=True)
        else:
            tool = self._tools.get(call.name)
            input_model = tool.input_model.model_validate(call.arguments)
            try:
                result = await tool.execute(ToolContext(run_id, workspace), input_model)
            except Exception as error:
                result = ToolResult(f"{type(error).__name__}: {error}", is_error=True)
        after_tool = AfterTool(run_id=run_id, call=call, result=result)
        await self._handlers.dispatch(after_tool)
        result = after_tool.result
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

    @staticmethod
    def _should_cancel(run_request: RunRequest) -> bool:
        return run_request.cancellation is not None and run_request.cancellation.is_cancelled

    async def _finish_cancelled(self, run_id: str, reason: str, model_calls: int) -> RunResult:
        await self._handlers.dispatch(
            RunCancelled(run_id=run_id, reason=reason, model_calls=model_calls)
        )
        self._checkpoints.append(
            run_id,
            RunEvent("RunCancelled", {"reason": reason}),
            RunStatus.CANCELLED,
        )
        return RunResult(run_id, RunStatus.CANCELLED, reason, model_calls)
