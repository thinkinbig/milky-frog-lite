from __future__ import annotations

from pathlib import Path

from milky_frog.core.policy import ToolPolicy
from milky_frog.core.runtime.execute_tool import execute_tool
from milky_frog.core.sandbox import Sandbox
from milky_frog.domain import (
    ApprovalDecision,
    ApprovalVerdict,
    RunCancellation,
    RunResult,
    RunState,
    ToolCall,
    ToolDecision,
    ToolResult,
    is_cancelled,
)
from milky_frog.events.emitter import RunEmitter
from milky_frog.harness.budget import TokenBudget
from milky_frog.harness.tools import ToolRegistry
from milky_frog.tokens import TokenCounter


class ToolStepExecutor:
    """Unified policy check → execute → notify path for one Tool call.

    Shared by ``AgentLoop`` (inline turns) and ``AgentHarness`` (resume approval
    resolution) so authorization semantics stay in one module.
    """

    def __init__(
        self,
        tools: ToolRegistry,
        emitter: RunEmitter,
        policy: ToolPolicy,
        budget: TokenBudget | None = None,
    ) -> None:
        self._tools = tools
        self._emitter = emitter
        self._policy = policy
        self._budget = budget

    async def run_with_policy(
        self,
        run_id: str,
        state: RunState,
        sandbox: Sandbox,
        call: ToolCall,
        cancellation: RunCancellation | None,
    ) -> ToolResult | RunResult:
        """Notify observers, check policy inline, then execute or pause."""
        if is_cancelled(cancellation):
            return await self._emitter.finish_cancelled(state)

        await self._emitter.before_tool(run_id, call)

        decision = self._policy.decide(call)
        if decision is ToolDecision.DENY:
            return ToolResult("denied by tool policy", is_error=True)
        if decision is ToolDecision.NEEDS_APPROVAL:
            return await self._emitter.finish_approval_needed(state, call)

        return await execute_tool(
            self._tools,
            run_id,
            state.workspace,
            sandbox,
            call,
            cancellation,
            token_counter=self._token_counter(),
        )

    async def resolve_pending(
        self,
        run_id: str,
        *,
        workspace: Path,
        sandbox: Sandbox,
        call: ToolCall,
        cancellation: RunCancellation | None,
        approval: ApprovalVerdict | None,
        state: RunState,
        require_verdict: bool = False,
    ) -> ToolResult | RunResult:
        """Resolve one pending approval on resume."""
        if approval is not None and approval.decision is ApprovalDecision.DENY:
            msg = "denied by user"
            if approval.denial_reason:
                msg += f" (reason: {approval.denial_reason})"
            return ToolResult(msg, is_error=True)
        if approval is not None and approval.decision is ApprovalDecision.APPROVE:
            return await execute_tool(
                self._tools,
                run_id,
                workspace,
                sandbox,
                call,
                cancellation,
                token_counter=self._token_counter(),
            )
        if require_verdict:
            return await self._emitter.finish_approval_needed(state, call)
        return await self.run_with_policy(
            run_id,
            state,
            sandbox,
            call,
            cancellation,
        )

    def _token_counter(self) -> TokenCounter | None:
        if self._budget is None:
            return None
        return self._budget.counter
