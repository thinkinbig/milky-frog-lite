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
    with_run_skills,
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
            run_extra = (run_request.skill_content,) if run_request.skill_content else ()
            state = start_run(
                RunState(
                    run_id=run_id,
                    workspace=workspace,
                    run_extra=run_extra,
                    selected_skills=run_request.selected_skills,
                    run_kind=run_request.run_kind,
                    parent_run_id=run_request.parent_run_id,
                ),
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
        run_extra: tuple[str, ...] | None = None,
        selected_skills: tuple[str, ...] | None = None,
    ) -> RunResult:
        """Advance an existing Run: load snapshot, repair, then advance.

        ``run_extra is None`` preserves the persisted eager system-prompt
        sections (skills survive resume); a tuple — including ``()`` — replaces
        them, so a caller can re-apply or clear activated Skills mid-run.
        ``run_extra`` and ``selected_skills`` must be supplied together, so
        the injected instructions and observable metadata cannot diverge.
        """
        if (run_extra is None) != (selected_skills is None):
            raise ResumeError("run_extra and selected_skills must be supplied together")
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
                if run_extra is not None and selected_skills is not None:
                    state = with_run_skills(state, run_extra, selected_skills)
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
                    verdicts={},
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
        """Release a Run paused on ``WAITING_FOR_APPROVAL`` — one verdict,
        one tool call.

        ``approval`` applies only to the **first** currently pending call.
        If more tool calls are still pending after executing (or denying) that
        one, the Run re-halts with ``WAITING_FOR_APPROVAL`` so the user can
        decide per call. Use ``respond_approvals`` to give different pending
        calls different verdicts in a single batch.
        """
        try:
            with self._checkpoints.claim(run_id):
                stored = self._require_run(run_id)
                if stored.status is not RunStatus.WAITING_FOR_APPROVAL:
                    raise ResumeError(f"Run {run_id} is not waiting for tool approval")

                state = self._checkpoints.load_state(run_id)
                pending = unmatched_tool_calls(state.messages)
                verdicts = {pending[0].id: approval}
                return await self._advance_prepared(
                    run_id,
                    stored,
                    state,
                    max_model_calls=max_model_calls,
                    cancellation=cancellation,
                    prompt=None,
                    verdicts=verdicts,
                )
        except RunClaimError as error:
            raise ResumeError(str(error)) from error

    async def respond_approvals(
        self,
        run_id: str,
        *,
        max_model_calls: int,
        verdicts: dict[str, ApprovalVerdict],
        cancellation: RunCancellation | None = None,
    ) -> RunResult:
        """Release a Run paused on ``WAITING_FOR_APPROVAL`` with a verdict per call.

        ``verdicts`` maps ``ToolCall.id`` to its individual ``ApprovalVerdict``.
        Approved calls execute concurrently; denied calls are skipped without
        executing. Pending calls with no entry in ``verdicts`` stay pending —
        the Run re-halts, exposing exactly those.
        """
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
                    verdicts=verdicts,
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
        verdicts: dict[str, ApprovalVerdict],
    ) -> RunResult:
        sandbox = self._make_sandbox(stored.workspace)
        await self._hub.before_resume(run_id, prompt, stored.status, state)
        if self._budget is not None:
            self._budget.init_for_workspace(stored.workspace)
        self._checkpoints.prepare_resume(run_id, stored.updated_at, state)

        plan = PreparedRun(state=state, sandbox=sandbox)
        resolved = await self._apply_approvals(plan, run_id, cancellation, verdicts=verdicts)
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
        *,
        verdicts: dict[str, ApprovalVerdict],
    ) -> PreparedRun | RunResult:
        """Resolve tool calls pending approval, using a verdict per call_id.

        Calls with a verdict run concurrently (approved) or are skipped
        (denied); calls with no entry in ``verdicts`` stay pending and cause a
        re-halt exposing exactly those — an empty ``verdicts`` map (the
        ``resume()`` path) re-halts on everything still pending, matching the
        Run's own ``WAITING_FOR_APPROVAL`` state.
        """
        pending = unmatched_tool_calls(plan.state.messages)
        if not pending:
            return plan
        if cancellation is not None and cancellation.is_cancelled:
            return await self._hub.finish_cancelled(plan.state)

        decided = [(call, verdicts[call.id]) for call in pending if call.id in verdicts]
        still_pending = tuple(call for call in pending if call.id not in verdicts)

        if decided:
            # Every decided call gets a ``before_tool``, denials included: a
            # denial still produces a ``ToolResult`` and an ``after_tool``, and a
            # result whose opener never fired leaves subscribers with an orphan
            # (the TUI drops the tool card, so a denied bash call renders as a
            # bare "denied by user" with no command). Matches
            # ``AgentLoop._execute_decided_batch``, which also opens DENY calls.
            for call, _verdict in decided:
                await self._hub.before_tool(run_id, call)
            batch = [
                (
                    call,
                    self._tool_step.execute_verdict(
                        run_id, plan.state.workspace, plan.sandbox, call, cancellation, verdict
                    ),
                )
                for call, verdict in decided
            ]
            resolved, cancelled = await self._tool_step.resolve_batch(batch)

            for call, outcome in resolved:
                plan = PreparedRun(
                    state=append_tool_result(plan.state, call, outcome),
                    sandbox=plan.sandbox,
                )
                await self._hub.after_tool(run_id, call, outcome, plan.state)

            if cancelled:
                return await self._hub.finish_cancelled(plan.state)

        if still_pending:
            return await self._hub.finish_approval_needed(plan.state, still_pending)
        return plan
