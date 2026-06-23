from __future__ import annotations

from milky_frog.checkpoint import CheckpointStore
from milky_frog.domain import RunStatus
from milky_frog.handlers.context import HandlerContext
from milky_frog.handlers.dispatcher import BaseHandler, EventDispatcher
from milky_frog.handlers.events import (
    RunAfterModel,
    RunAfterTool,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunPaused,
    RunStarted,
    TerminalRunEvent,
)

_PRIORITY = 100


class CheckpointHandler(BaseHandler):
    """Persists RunState snapshots at each durable lifecycle boundary.

    Registered at priority 100 so checkpointing always precedes other observers —
    a handler seeing RunCompleted can trust the checkpoint is already written.
    """

    def __init__(self, store: CheckpointStore) -> None:
        self._store = store

    def register(self, registry: EventDispatcher) -> None:
        registry.on(RunStarted, priority=_PRIORITY)(self._on_run_started)
        registry.on(RunAfterModel, priority=_PRIORITY)(self._on_after_model)
        registry.on(RunAfterTool, priority=_PRIORITY)(self._on_after_tool)
        registry.on(RunCompleted, priority=_PRIORITY)(self._on_terminal)
        registry.on(RunPaused, priority=_PRIORITY)(self._on_terminal)
        registry.on(RunCancelled, priority=_PRIORITY)(self._on_terminal)
        registry.on(RunFailed, priority=_PRIORITY)(self._on_terminal)

    async def _on_run_started(self, event: RunStarted, ctx: HandlerContext) -> None:
        self._store.save_state(event.run_id, event.state, status=RunStatus.RUNNING)

    async def _on_after_model(self, event: RunAfterModel, ctx: HandlerContext) -> None:
        self._store.save_state(event.run_id, event.state)

    async def _on_after_tool(self, event: RunAfterTool, ctx: HandlerContext) -> None:
        self._store.save_state(event.run_id, event.state)

    async def _on_terminal(self, event: TerminalRunEvent, ctx: HandlerContext) -> None:
        self._store.save_state(
            event.run_id,
            event.state,
            status=event.result.status,
            final_message=event.result.final_message,
        )
