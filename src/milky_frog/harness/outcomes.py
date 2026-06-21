from __future__ import annotations

from milky_frog.domain import RunResult, RunState, RunStatus, SteeringChannel
from milky_frog.harness.emitter import RunEmitter
from milky_frog.harness.steering import SteeringPolicy


class RunOutcomes:
    """Terminal Run states, emitted through the shared RunEmitter."""

    def __init__(self, emitter: RunEmitter, steering: SteeringPolicy) -> None:
        self._emitter = emitter
        self._steering = steering

    async def finish_completed(
        self,
        state: RunState,
        final_message: str,
        *,
        steering: SteeringChannel | None,
    ) -> RunResult | RunState:
        # One last steering drain before persisting COMPLETED — catches lines
        # that arrived during the completion decision window, which would
        # otherwise be silently dropped by the producer's stop() drain.
        steered = self._steering.absorb_turn_boundary(state, steering)
        if SteeringPolicy.added_turns(state, steered):
            return steered
        result = RunResult(
            state.run_id,
            RunStatus.COMPLETED,
            final_message,
            state.completed_model_calls,
            state.usage,
        )
        await self._emitter.run_completed(state, final_message, result)
        return result

    async def finish_paused(self, state: RunState, max_model_calls: int) -> RunResult:
        message = f"model call limit reached ({max_model_calls})"
        result = RunResult(
            state.run_id,
            RunStatus.PAUSED_LIMIT,
            message,
            state.completed_model_calls,
            state.usage,
        )
        await self._emitter.run_paused(state, message, result)
        return result

    async def finish_cancelled(self, state: RunState, reason: str = "cancelled") -> RunResult:
        result = RunResult(
            state.run_id,
            RunStatus.CANCELLED,
            reason,
            state.completed_model_calls,
            state.usage,
        )
        await self._emitter.run_cancelled(state, reason, result)
        return result
