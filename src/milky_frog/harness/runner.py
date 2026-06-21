from __future__ import annotations

import asyncio
from uuid import uuid4

from milky_frog.checkpoint import CheckpointStore, RunClaimError
from milky_frog.domain import (
    ModelRequest,
    RunCancellation,
    RunRequest,
    RunResult,
    RunState,
    SteeringChannel,
)
from milky_frog.handlers import HandlerRegistry
from milky_frog.harness.cancellation import ToolRunCancelled, is_cancelled
from milky_frog.harness.emitter import RunEmitter
from milky_frog.harness.outcomes import RunOutcomes
from milky_frog.harness.resume import (
    CompleteShortcutPlan,
    ResumeError,
    ResumeGate,
)
from milky_frog.harness.sandbox import LocalSandbox, Sandbox
from milky_frog.harness.state import (
    append_model_response,
    append_tool_result,
    append_user_message,
    start_run,
)
from milky_frog.harness.steering import SteeringPolicy
from milky_frog.harness.streaming import ModelStreamer
from milky_frog.harness.tool_runner import ToolRunner
from milky_frog.harness.tools import ToolRegistry
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
        self._tools = tools
        self._checkpoints = checkpoints
        self._emitter = RunEmitter(checkpoints, handlers)
        self._steering = SteeringPolicy(self._append_user_message)
        self._outcomes = RunOutcomes(self._emitter, self._steering)
        self._resume_gate = ResumeGate(checkpoints, self._steering)
        self._streamer = ModelStreamer(model, self._emitter)
        self._tool_runner = ToolRunner(tools, self._emitter)

    async def run(self, run_request: RunRequest) -> RunResult:
        """Start a fresh Run: seed the transcript from the prompt, then advance."""
        run_id = uuid4().hex
        workspace = run_request.workspace.resolve(strict=True)
        with self._checkpoints.claim(run_id):
            self._checkpoints.create_run(run_id, workspace)
            state = start_run(RunState(run_id=run_id, workspace=workspace), run_request.prompt)
            await self._emitter.run_started(run_id, run_request, state)
            return await self._advance(
                state,
                LocalSandbox(workspace),
                run_request.cancellation,
                run_request.max_model_calls,
                run_request.steering,
            )

    async def resume(
        self,
        run_id: str,
        *,
        max_model_calls: int,
        cancellation: RunCancellation | None = None,
        prompt: str | None = None,
        steering: SteeringChannel | None = None,
    ) -> RunResult:
        """Advance an existing Run: load its snapshot and repair any interrupted
        Tool, then either pick up its pending work (no prompt) or append a new user
        turn (with prompt) and advance.

        Without a prompt, only a Run with pending work (PAUSED_LIMIT, CANCELLED,
        or an orphaned RUNNING / WAITING_*) can be advanced. With a prompt, any
        terminal Run can — a finished conversation is continued by adding the
        next user message.
        """
        try:
            with self._checkpoints.claim(run_id):
                stored = self._checkpoints.get_run(run_id)
                stored = ResumeGate.validate(stored, run_id, prompt)
                sandbox = LocalSandbox(stored.workspace)
                plan = self._resume_gate.prepare(
                    run_id,
                    stored,
                    sandbox=sandbox,
                    prompt=prompt,
                    steering=steering,
                    updated_at=stored.updated_at,
                )
                if isinstance(plan, CompleteShortcutPlan):
                    result_or_state = await self._outcomes.finish_completed(
                        plan.state,
                        plan.tail,
                        steering=steering,
                    )
                    if isinstance(result_or_state, RunState):
                        return await self._advance(
                            result_or_state,
                            plan.sandbox,
                            cancellation,
                            max_model_calls,
                            steering,
                        )
                    return result_or_state
                return await self._advance(
                    plan.state,
                    plan.sandbox,
                    cancellation,
                    max_model_calls,
                    steering,
                )
        except RunClaimError as error:
            raise ResumeError(str(error)) from error

    async def _advance(
        self,
        state: RunState,
        sandbox: Sandbox,
        cancellation: RunCancellation | None,
        max_model_calls: int,
        steering: SteeringChannel | None = None,
    ) -> RunResult:
        """Drive the model and Tool loop for at most ``max_model_calls`` fresh
        model calls, threading ``RunState`` and growing it through state mutators.
        Steering lines drained between turns are folded in as user turns, and a
        non-empty drain keeps the loop going instead of completing.
        """
        run_id = state.run_id
        try:
            calls = 0
            while calls < max_model_calls:
                if is_cancelled(cancellation):
                    return await self._outcomes.finish_cancelled(state)
                state = self._steering.absorb_turn_boundary(state, steering)
                request = ModelRequest(state.messages, self._tools.schemas())
                await self._emitter.before_model(run_id, request)
                response = await self._streamer.consume(run_id, cancellation, request)
                await self._emitter.after_model(run_id, request, response)
                state = append_model_response(state, response)
                self._emitter.persist(state)
                calls += 1

                if not response.tool_calls:
                    steered = self._steering.absorb_turn_boundary(state, steering)
                    if SteeringPolicy.added_turns(state, steered):
                        state = steered
                        continue
                    result_or_state = await self._outcomes.finish_completed(
                        state,
                        response.content,
                        steering=steering,
                    )
                    if isinstance(result_or_state, RunState):
                        state = result_or_state
                        continue
                    return result_or_state

                for call in response.tool_calls:
                    if is_cancelled(cancellation):
                        return await self._outcomes.finish_cancelled(state)
                    try:
                        result = await self._tool_runner.execute(
                            run_id, state.workspace, sandbox, call, cancellation
                        )
                    except ToolRunCancelled:
                        return await self._outcomes.finish_cancelled(state)
                    state = append_tool_result(state, call, result)
                    self._emitter.persist(state)

            return await self._outcomes.finish_paused(state, max_model_calls)
        except asyncio.CancelledError:
            if is_cancelled(cancellation):
                return await self._outcomes.finish_cancelled(state)
            raise
        except Exception as error:
            await self._emitter.run_failed(state, error)
            raise

    def _append_user_message(self, state: RunState, content: str) -> RunState:
        """Append a user turn, persist it, and return the updated state."""
        state = append_user_message(state, content)
        self._emitter.persist(state)
        return state
