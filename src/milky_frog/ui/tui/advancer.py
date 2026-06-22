from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Protocol

from textual.message import Message

from milky_frog.domain import (
    ApprovalDecision,
    ResumeError,
    RunCancellation,
    RunRequest,
    RunResult,
    RunStatus,
)
from milky_frog.project import load_project_config
from milky_frog.runtime import MilkyFrog
from milky_frog.ui.tui.messages import RunError, RunFinished


class WidgetChannel(Protocol):
    """Post a Textual message to the owning widget or app."""

    def post_message(self, message: Message) -> bool: ...


class RunAdvancer:
    """Orchestration state for one interactive TUI conversation.

    Holds ``run_id``, busy flag, cancellation token, and pending-approval run_id.
    Posts ``RunFinished`` / ``RunError`` messages to the ``WidgetChannel``.
    Harness failures are surfaced by ``TextualStreamRenderer`` on ``RunFailed``;
    this advancer only posts ``RunError`` for ``ResumeError`` and pre-harness checks.
    ``MilkyFrogApp`` ``@work`` workers delegate their coroutine bodies to
    ``do_run`` / ``do_approve`` so the App stays a pure widget layer.
    """

    def __init__(self, frog: MilkyFrog, queue: WidgetChannel) -> None:
        self.frog = frog
        self._queue = queue
        self.run_id: str | None = None
        self.busy: bool = False
        self.cancellation: RunCancellation | None = None
        self.pending_approval: str | None = None

    def begin(self) -> RunCancellation:
        """Mark as busy and mint a fresh cancellation token for the caller."""
        self.busy = True
        token = RunCancellation()
        self.cancellation = token
        return token

    def cancel(self) -> None:
        """Cooperatively signal the in-flight Run to stop."""
        if self.cancellation is not None:
            self.cancellation.cancel()

    def resolve_approval(self, answer: str) -> ApprovalDecision | None:
        """Parse a y/n reply; return ``None`` when the answer is unrecognised."""
        if answer in {"y", "yes", "a", "approve"}:
            return ApprovalDecision.APPROVE
        if answer in {"n", "no", "d", "deny"}:
            return ApprovalDecision.DENY
        return None

    async def do_run(self, task: str, run_id: str | None) -> None:
        """Advance the harness (new Run or resume with a prompt), then post result."""
        cancellation = self.cancellation
        frog = self.frog
        try:
            if run_id is None:
                config = load_project_config(Path.cwd())
                result = await frog.harness.run(
                    RunRequest(
                        task,
                        Path.cwd(),
                        max_model_calls=config.max_model_calls,
                        cancellation=cancellation,
                    )
                )
            else:
                stored = frog.checkpoints.get_run(run_id)
                if stored is None:
                    self._queue.post_message(RunError(f"unknown Run: {run_id}"))
                    return
                config = load_project_config(stored.workspace)
                result = await frog.harness.resume(
                    run_id,
                    max_model_calls=config.max_model_calls,
                    prompt=task,
                    cancellation=cancellation,
                )
            self._finish(result)
        except asyncio.CancelledError:
            self._cancelled(run_id)
        except ResumeError as error:
            self._queue.post_message(RunError(str(error)))
        except Exception:
            # Harness failures emit RunFailed first; the stream renderer posts RunError.
            pass
        finally:
            self.busy = False
            self.cancellation = None

    async def do_approve(self, run_id: str, decision: ApprovalDecision) -> None:
        """Resume a paused Run with the user's approval verdict."""
        cancellation = self.cancellation
        frog = self.frog
        try:
            stored = frog.checkpoints.get_run(run_id)
            if stored is None:
                self._queue.post_message(RunError(f"unknown Run: {run_id}"))
                return
            config = load_project_config(stored.workspace)
            result = await frog.harness.resume(
                run_id,
                max_model_calls=config.max_model_calls,
                approval=decision,
                cancellation=cancellation,
            )
            self._finish(result)
        except asyncio.CancelledError:
            self._cancelled(run_id)
        except ResumeError as error:
            self._queue.post_message(RunError(str(error)))
        except Exception:
            # Harness failures emit RunFailed first; the stream renderer posts RunError.
            pass
        finally:
            self.busy = False
            self.cancellation = None

    def _finish(self, result: RunResult) -> None:
        self.run_id = result.run_id
        self._queue.post_message(
            RunFinished(result=result, status=result.status, message=result.final_message)
        )

    def _cancelled(self, run_id: str | None) -> None:
        self._queue.post_message(
            RunFinished(
                result=RunResult(run_id or "unknown", RunStatus.CANCELLED, "cancelled", 0),
                status=RunStatus.CANCELLED,
                message="Cancelled the current task.",
            )
        )
