from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from milky_frog.checkpoint import CheckpointStore, RunClaimError
from milky_frog.domain import (
    ApprovalDecision,
    ApprovalVerdict,
    ResumeError,
    RunCancellation,
    RunRequest,
    RunResult,
    RunState,
    RunStatus,
    ToolCall,
    ToolResult,
)
from milky_frog.handlers import ApprovalResult, BlockResult, EventDispatcher
from milky_frog.harness.agent_loop import AgentLoop
from milky_frog.harness.agent_loop import execute_tool as _execute_tool
from milky_frog.harness.emitter import RunEmitter
from milky_frog.harness.sandbox import LocalSandbox, Sandbox, SandboxFactory
from milky_frog.harness.state import (
    append_tool_result,
    append_user_message,
    seal,
    start_run,
    unmatched_tool_calls,
)
from milky_frog.harness.tools import ToolRegistry
from milky_frog.models import Model


@dataclass(frozen=True, slots=True)
class PreparedRun:
    """State and sandbox prepared for ``AgentLoop.advance`` after a resume."""

    state: RunState
    sandbox: Sandbox


class AgentHarness:
    """Prepares ``RunState`` (seed / repair / approval resolution), then
    delegates the model→Tool→model loop to ``AgentLoop`` which emits
    lifecycle+streaming events directly to the shared event bus.

    ``AgentLoop`` handles everything inside a turn (model call, tool execution,
    policy checks via bus subscribers).  Harness only owns pre-loop concerns:
    checkpoint claiming, state preparation, and pending-approval resolution.
    """

    def __init__(
        self,
        model: Model,
        tools: ToolRegistry,
        checkpoints: CheckpointStore,
        handlers: EventDispatcher,
        sandbox_factory: SandboxFactory = LocalSandbox,
    ) -> None:
        self._model = model
        self._tools = tools
        self._checkpoints = checkpoints
        self._sandbox_factory = sandbox_factory
        self._handlers = handlers
        self._emitter = RunEmitter(handlers)
        self._agent_loop = AgentLoop(model, tools, self._emitter)

    async def run(self, run_request: RunRequest) -> RunResult:
        """Start a fresh Run: seed the transcript, then advance."""
        run_id = uuid4().hex
        workspace = run_request.workspace.resolve(strict=True)
        with self._checkpoints.claim(run_id):
            self._checkpoints.create_run(run_id, workspace)
            extra_sections = await self._emitter.run_before_start(run_id, run_request, workspace)
            state = start_run(
                RunState(run_id=run_id, workspace=workspace),
                run_request.prompt,
                extra_sections,
            )
            await self._emitter.run_started(run_id, run_request, state)
            return await self._agent_loop.advance(
                state,
                self._sandbox_factory(workspace),
                cancellation=run_request.cancellation,
                max_calls=run_request.max_model_calls,
            )

    async def resume(
        self,
        run_id: str,
        *,
        max_model_calls: int,
        cancellation: RunCancellation | None = None,
        prompt: str | None = None,
        approval: ApprovalVerdict | None = None,
    ) -> RunResult:
        """Advance an existing Run: load snapshot, repair, resolve approvals,
        then advance."""
        try:
            with self._checkpoints.claim(run_id):
                stored = self._checkpoints.get_run(run_id)
                if stored is None:
                    raise ResumeError(f"unknown Run: {run_id}")

                sandbox = self._sandbox_factory(stored.workspace)

                await self._emitter.before_resume(run_id, prompt, stored.status)

                state = self._checkpoints.load_state(run_id)
                if stored.status is not RunStatus.WAITING_FOR_APPROVAL:
                    state, _ = seal(state)
                if prompt is not None:
                    state = append_user_message(state, prompt)
                self._checkpoints.prepare_resume(run_id, stored.updated_at, state)

                plan = PreparedRun(state=state, sandbox=sandbox)
                resolved = await self._apply_approvals(
                    plan, run_id, sandbox, cancellation, approval
                )
                if isinstance(resolved, RunResult):
                    return resolved
                return await self._agent_loop.advance(
                    resolved.state,
                    resolved.sandbox,
                    cancellation=cancellation,
                    max_calls=max_model_calls,
                )
        except RunClaimError as error:
            raise ResumeError(str(error)) from error

    # ── Pre-loop approval resolution ─────────────────────────────────

    async def _apply_approvals(
        self,
        plan: PreparedRun,
        run_id: str,
        sandbox: Sandbox,
        cancellation: RunCancellation | None,
        approval: ApprovalVerdict | None,
    ) -> PreparedRun | RunResult:
        """Resolve tool calls that were pending approval on resume."""
        pending = unmatched_tool_calls(plan.state.messages)
        if not pending:
            return plan
        for call in pending:
            if cancellation is not None and cancellation.is_cancelled:
                return await self._emitter.finish_cancelled(plan.state)

            resolved = await self._resolve_pending_call(
                plan, run_id, sandbox, call, cancellation, approval
            )
            if isinstance(resolved, RunResult):
                return resolved
            plan = PreparedRun(
                state=append_tool_result(plan.state, call, resolved),
                sandbox=plan.sandbox,
            )
            await self._emitter.after_tool(run_id, call, resolved, plan.state)
        return plan

    async def _resolve_pending_call(
        self,
        plan: PreparedRun,
        run_id: str,
        sandbox: Sandbox,
        call: ToolCall,
        cancellation: RunCancellation | None,
        approval: ApprovalVerdict | None,
    ) -> ToolResult | RunResult:
        """Decide one pending call's fate; ``RunResult`` ends the Run."""
        if approval is not None and approval.decision is ApprovalDecision.DENY:
            msg = "denied by user"
            if approval.denial_reason:
                msg += f" (reason: {approval.denial_reason})"
            return ToolResult(msg, is_error=True)
        if approval is not None and approval.decision is ApprovalDecision.APPROVE:
            result: ToolResult = await _execute_tool(
                self._tools, run_id, plan.state.workspace, sandbox, call, cancellation
            )
            return result

        # No verdict: fall back to the tool policy, which may pause again.
        check_results = await self._emitter.before_tool(run_id, call)
        blocked = [r for r in check_results if isinstance(r, BlockResult)]
        approvals = [r for r in check_results if isinstance(r, ApprovalResult)]
        if blocked:
            return ToolResult(blocked[0].reason, is_error=True)
        if approvals:
            return await self._emitter.finish_approval_needed(plan.state, call)
        r: ToolResult = await _execute_tool(
            self._tools, run_id, plan.state.workspace, sandbox, call, cancellation
        )
        return r
