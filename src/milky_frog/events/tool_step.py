from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Sequence
from pathlib import Path
from typing import cast

from milky_frog.core.policy import ToolPolicy
from milky_frog.core.runtime.execute_tool import execute_tool
from milky_frog.core.sandbox import Sandbox
from milky_frog.domain import (
    ApprovalDecision,
    ApprovalVerdict,
    RunCancellation,
    ToolCall,
    ToolDecision,
    ToolResult,
    ToolRunCancelled,
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

    def decide(self, call: ToolCall) -> ToolDecision:
        """Pure, synchronous peek at the policy decision — no execution, no I/O.

        Lets a caller pre-flight a whole batch of calls (e.g. to decide whether
        the batch is safe to run concurrently) before committing to execution.
        """
        return self._policy.decide(call)

    async def execute_decided(
        self,
        run_id: str,
        workspace: Path,
        sandbox: Sandbox,
        call: ToolCall,
        cancellation: RunCancellation | None,
        decision: ToolDecision,
    ) -> ToolResult:
        """Execute a call whose policy decision is already known.

        ``decision`` must not be ``NEEDS_APPROVAL`` — callers that batch
        pre-flight decisions via ``decide()`` filter those out before reaching
        here (approval is a whole-Run pause, not a per-call one).
        """
        if decision is ToolDecision.DENY:
            return ToolResult("denied by tool policy", is_error=True)
        return await execute_tool(
            self._tools,
            run_id,
            workspace,
            sandbox,
            call,
            cancellation,
            token_counter=self._token_counter(),
        )

    async def execute_verdict(
        self,
        run_id: str,
        workspace: Path,
        sandbox: Sandbox,
        call: ToolCall,
        cancellation: RunCancellation | None,
        verdict: ApprovalVerdict,
    ) -> ToolResult:
        """Execute a call whose user approval verdict is already known.

        Mirrors ``execute_decided`` (policy-known execution) for the
        approval-resolution path: ``DENY`` short-circuits to a denial
        result, ``APPROVE`` runs the Tool. Used by ``AgentHarness._apply_approvals``
        to resolve a batch of ``respond_approval(s)`` verdicts, including
        concurrently for the approved subset.
        """
        if verdict.decision is ApprovalDecision.DENY:
            msg = "denied by user"
            if verdict.denial_reason:
                msg += f" (reason: {verdict.denial_reason})"
            return ToolResult(msg, is_error=True)
        return await execute_tool(
            self._tools,
            run_id,
            workspace,
            sandbox,
            call,
            cancellation,
            token_counter=self._token_counter(),
        )

    async def resolve_batch(
        self, batch: Sequence[tuple[ToolCall, Awaitable[ToolResult]]]
    ) -> tuple[list[tuple[ToolCall, ToolResult]], bool]:
        """Run a batch of already-started Tool executions concurrently.

        Returns the ``(call, result)`` pairs for every call that actually
        completed, in request order, plus whether any call in the batch was
        cancelled. A cancelled sibling never discards a completed one — the
        caller folds every returned pair into ``state`` (and checkpoints it)
        before honoring the cancellation, so a Tool that already ran (e.g. a
        completed ``write_file``) is never silently dropped from the
        transcript. ``execute_tool`` guarantees each awaitable raises nothing
        but ``ToolRunCancelled``, so every other result is safe to treat as
        a ``ToolResult``.
        """
        if not batch:
            return [], False
        calls = [call for call, _awaitable in batch]
        results = await asyncio.gather(
            *(awaitable for _call, awaitable in batch), return_exceptions=True
        )
        cancelled = False
        resolved: list[tuple[ToolCall, ToolResult]] = []
        for call, result in zip(calls, results, strict=True):
            if isinstance(result, ToolRunCancelled):
                cancelled = True
                continue
            resolved.append((call, cast(ToolResult, result)))
        return resolved, cancelled

    def _token_counter(self) -> TokenCounter | None:
        if self._budget is None:
            return None
        return self._budget.counter
