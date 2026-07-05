from __future__ import annotations

import asyncio
from pathlib import Path

from milky_frog.checkpoint import StoredRun
from milky_frog.core.runtime.checkpoint import RunCheckpointFacade
from milky_frog.domain import (
    ApprovalVerdict,
    ResumeError,
    RunCancellation,
    RunRequest,
    RunResult,
)
from milky_frog.harness.harness import AgentHarness
from milky_frog.project import load_project_config


class ForegroundRun:
    """Foreground Run orchestration: busy flag, cancellation, Harness routing.

    Pure runtime coordination — no Settings, OpenAI, or handler wiring.
    ``AgentSession`` constructs a ``ForegroundRun`` and delegates here.
    """

    def __init__(
        self,
        harness: AgentHarness,
        checkpoints: RunCheckpointFacade,
        *,
        interactive: bool = False,
    ) -> None:
        self._harness = harness
        self._checkpoints = checkpoints
        self._interactive = interactive

        self.run_id: str | None = None
        self.busy: bool = False
        self.pending_approval: str | None = None
        self._cancellation: RunCancellation | None = None

    @property
    def harness(self) -> AgentHarness:
        return self._harness

    @property
    def checkpoints(self) -> RunCheckpointFacade:
        return self._checkpoints

    def cancel(self) -> None:
        if self._cancellation is not None:
            self._cancellation.cancel()

    def shutdown(self) -> None:
        """Stop and checkpoint an in-flight Run without tearing down session resources."""
        self.cancel()
        run_id = self.run_id
        if run_id is None:
            return
        self._checkpoints.seal_interrupt(run_id)

    async def start_new(
        self,
        task: str,
        workspace: Path | None = None,
        *,
        skill_content: str | None = None,
    ) -> RunResult:
        workspace = (workspace or Path.cwd()).resolve(strict=True)
        project_cfg = load_project_config(workspace)
        self.busy = True
        self._cancellation = RunCancellation()
        try:
            max_calls = 0 if self._interactive else project_cfg.max_model_calls
            try:
                result = await self._harness.run(
                    RunRequest(
                        task,
                        workspace,
                        max_model_calls=max_calls,
                        cancellation=self._cancellation,
                        skill_content=skill_content,
                    )
                )
            except asyncio.CancelledError:
                self.shutdown()
                raise
            self.run_id = result.run_id
            return result
        finally:
            self.busy = False
            self._cancellation = None

    async def continue_with(
        self,
        run_id: str,
        *,
        prompt: str | None = None,
        run_extra: tuple[str, ...] | None = None,
    ) -> RunResult:
        stored = self._resolve_stored_run(run_id)
        project_cfg = load_project_config(stored.workspace)
        self.busy = True
        self._cancellation = RunCancellation()
        self.run_id = stored.run_id
        try:
            max_calls = 0 if self._interactive else project_cfg.max_model_calls
            try:
                result = await self._harness.resume(
                    stored.run_id,
                    max_model_calls=max_calls,
                    cancellation=self._cancellation,
                    prompt=prompt,
                    run_extra=run_extra,
                )
            except asyncio.CancelledError:
                self.shutdown()
                raise
            self.run_id = result.run_id
            return result
        finally:
            self.busy = False
            self._cancellation = None

    async def respond_approval(self, run_id: str, verdict: ApprovalVerdict) -> RunResult:
        from milky_frog.domain import RunStatus

        stored = self._resolve_stored_run(run_id)
        if stored.status is not RunStatus.WAITING_FOR_APPROVAL:
            raise ResumeError(f"Run {stored.run_id} is not waiting for tool approval")
        project_cfg = load_project_config(stored.workspace)
        self.busy = True
        self._cancellation = RunCancellation()
        self.run_id = stored.run_id
        try:
            max_calls = 0 if self._interactive else project_cfg.max_model_calls
            try:
                result = await self._harness.respond_approval(
                    stored.run_id,
                    max_model_calls=max_calls,
                    cancellation=self._cancellation,
                    approval=verdict,
                )
            except asyncio.CancelledError:
                self.shutdown()
                raise
            self.run_id = result.run_id
            return result
        finally:
            self.busy = False
            self._cancellation = None

    def _resolve_stored_run(self, run_id: str) -> StoredRun:

        try:
            resolved = self._checkpoints.resolve_run_id(run_id)
        except LookupError as error:
            raise ResumeError(f"unknown Run: {run_id}") from error
        except ValueError as error:
            raise ResumeError(f"ambiguous Run prefix: {run_id}") from error
        stored = self._checkpoints.get_run(resolved)
        if stored is None:
            raise ResumeError(f"unknown Run: {resolved}")
        return stored

    @staticmethod
    def cancelled_result(run_id: str | None) -> RunResult:
        from milky_frog.domain import RunStatus

        return RunResult(run_id or "unknown", RunStatus.CANCELLED, "cancelled", 0)
