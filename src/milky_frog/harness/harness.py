from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from milky_frog.adapters.local import LocalSandbox
from milky_frog.checkpoint import CheckpointStore, RunClaimError, StoredRun
from milky_frog.core.sandbox import Sandbox, SandboxFactory
from milky_frog.core.session_tool_policy import SessionToolPolicy
from milky_frog.domain import (
    ApprovalVerdict,
    ResumeError,
    RunCancellation,
    RunRequest,
    RunResult,
    RunState,
    RunStatus,
)
from milky_frog.events import EventHub
from milky_frog.events.loop import AgentLoop
from milky_frog.events.tool_step import ToolStepExecutor
from milky_frog.harness.budget import TokenBudget
from milky_frog.harness.state import (
    append_tool_result,
    append_user_message,
    seal,
    start_run,
    unmatched_tool_calls,
)


@dataclass(frozen=True, slots=True)
class PreparedRun:
    """State and sandbox prepared for ``AgentLoop.advance`` after a resume."""

    state: RunState
    sandbox: Sandbox


class AgentHarness:
    """Prepares ``RunState`` (seed / repair / approval resolution), then
    delegates the model→Tool→model loop to an injected ``AgentLoop``.

    Runtime wiring (``AgentLoop``, ``ToolStepExecutor``, ``SessionToolPolicy``)
    lives in ``make_agent_harness`` so this class stays a thin coordinator.
    """

    def __init__(
        self,
        checkpoints: CheckpointStore,
        hub: EventHub,
        agent_loop: AgentLoop,
        tool_step: ToolStepExecutor,
        policy: SessionToolPolicy,
        sandbox_factory: SandboxFactory = LocalSandbox,
        budget: TokenBudget | None = None,
    ) -> None:
        self._checkpoints = checkpoints
        self._hub = hub
        self._agent_loop = agent_loop
        self._tool_step = tool_step
        self._policy = policy
        self._sandbox_factory = sandbox_factory
        self._budget = budget

    @property
    def policy(self) -> SessionToolPolicy:
        return self._policy

    async def run(self, run_request: RunRequest) -> RunResult:
        """Start a fresh Run: seed the transcript, then advance."""
        run_id = uuid4().hex
        workspace = run_request.workspace.resolve(strict=True)
        with self._checkpoints.claim(run_id):
            self._checkpoints.create_run(run_id, workspace)
            await self._hub.run_before_start(run_id, run_request, workspace)
            state = start_run(
                RunState(run_id=run_id, workspace=workspace),
                run_request.prompt,
            )
            await self._hub.run_started(run_id, run_request, state)
            if self._budget is not None:
                self._budget.init_for_workspace(workspace)
            sandbox = self._make_sandbox(workspace)
            return await self._agent_loop.advance(
                state,
                sandbox,
                cancellation=run_request.cancellation,
                max_calls=run_request.max_model_calls,
                budget=self._budget,
            )

    async def resume(
        self,
        run_id: str,
        *,
        max_model_calls: int,
        cancellation: RunCancellation | None = None,
        prompt: str | None = None,
    ) -> RunResult:
        """Advance an existing Run: load snapshot, repair, then advance."""
        try:
            with self._checkpoints.claim(run_id):
                stored = self._require_run(run_id)
                waiting_approval = stored.status is RunStatus.WAITING_FOR_APPROVAL
                if waiting_approval and prompt is not None:
                    raise ResumeError(
                        f"Run {run_id} is waiting for tool approval; "
                        "approve or deny the pending tool first"
                    )

                state = self._checkpoints.load_state(run_id)
                if not waiting_approval:
                    state, _ = seal(state)
                    if prompt is not None:
                        state = append_user_message(state, prompt)

                return await self._advance_prepared(
                    run_id,
                    stored,
                    state,
                    max_model_calls=max_model_calls,
                    cancellation=cancellation,
                    prompt=prompt,
                    approval=None,
                    require_verdict=waiting_approval,
                )
        except RunClaimError as error:
            raise ResumeError(str(error)) from error

    async def respond_approval(
        self,
        run_id: str,
        *,
        max_model_calls: int,
        approval: ApprovalVerdict,
        cancellation: RunCancellation | None = None,
    ) -> RunResult:
        """Release a Run paused on ``WAITING_FOR_APPROVAL`` with the user's verdict."""
        try:
            with self._checkpoints.claim(run_id):
                stored = self._require_run(run_id)
                if stored.status is not RunStatus.WAITING_FOR_APPROVAL:
                    raise ResumeError(f"Run {run_id} is not waiting for tool approval")

                state = self._checkpoints.load_state(run_id)
                return await self._advance_prepared(
                    run_id,
                    stored,
                    state,
                    max_model_calls=max_model_calls,
                    cancellation=cancellation,
                    prompt=None,
                    approval=approval,
                    require_verdict=False,
                )
        except RunClaimError as error:
            raise ResumeError(str(error)) from error

    def _require_run(self, run_id: str) -> StoredRun:
        stored = self._checkpoints.get_run(run_id)
        if stored is None:
            raise ResumeError(f"unknown Run: {run_id}")
        return stored

    async def _advance_prepared(
        self,
        run_id: str,
        stored: StoredRun,
        state: RunState,
        *,
        max_model_calls: int,
        cancellation: RunCancellation | None,
        prompt: str | None,
        approval: ApprovalVerdict | None,
        require_verdict: bool,
    ) -> RunResult:
        sandbox = self._make_sandbox(stored.workspace)
        await self._hub.before_resume(run_id, prompt, stored.status, stored.workspace)
        if self._budget is not None:
            self._budget.init_for_workspace(stored.workspace)
        self._checkpoints.prepare_resume(run_id, stored.updated_at, state)

        plan = PreparedRun(state=state, sandbox=sandbox)
        resolved = await self._apply_approvals(
            plan,
            run_id,
            cancellation,
            approval=approval,
            require_verdict=require_verdict,
        )
        if isinstance(resolved, RunResult):
            return resolved
        return await self._agent_loop.advance(
            resolved.state,
            resolved.sandbox,
            cancellation=cancellation,
            max_calls=max_model_calls,
            budget=self._budget,
        )

    def _make_sandbox(self, workspace: Path) -> Sandbox:
        """Build a ``Sandbox`` via the injected factory."""
        return self._sandbox_factory(workspace)

    async def _apply_approvals(
        self,
        plan: PreparedRun,
        run_id: str,
        cancellation: RunCancellation | None,
        approval: ApprovalVerdict | None,
        *,
        require_verdict: bool = False,
    ) -> PreparedRun | RunResult:
        """Resolve tool calls that were pending approval on resume."""
        pending = unmatched_tool_calls(plan.state.messages)
        if not pending:
            return plan
        for call in pending:
            if cancellation is not None and cancellation.is_cancelled:
                return await self._hub.finish_cancelled(plan.state)

            resolved = await self._tool_step.resolve_pending(
                run_id,
                workspace=plan.state.workspace,
                sandbox=plan.sandbox,
                call=call,
                cancellation=cancellation,
                approval=approval,
                state=plan.state,
                require_verdict=require_verdict,
            )
            if isinstance(resolved, RunResult):
                return resolved
            plan = PreparedRun(
                state=append_tool_result(plan.state, call, resolved),
                sandbox=plan.sandbox,
            )
            await self._hub.after_tool(run_id, call, resolved, plan.state)
        return plan
