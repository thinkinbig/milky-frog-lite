from __future__ import annotations

from typing import override

from milky_frog.checkpoint import CheckpointStore
from milky_frog.core.handlers import HandlerDeps
from milky_frog.domain import RunStatus
from milky_frog.events.events import (
    RunAfterModel,
    RunAfterTool,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunPaused,
    RunStarted,
    TerminalRunEvent,
)
from milky_frog.events.hub import EventHub, Handler

_PRIORITY = 100


class CheckpointHandler(Handler):
    """Persists RunState snapshots at each durable lifecycle boundary.

    Registered at priority 100 so checkpointing always precedes other observers —
    a handler seeing RunCompleted can trust the checkpoint is already written.
    """

    def __init__(self, store: CheckpointStore) -> None:
        self._store = store

    @override
    def register(self, hub: EventHub) -> None:
        hub.on(RunStarted, priority=_PRIORITY)(self._on_run_started)
        hub.on(RunAfterModel, priority=_PRIORITY)(self._on_after_model)
        hub.on(RunAfterTool, priority=_PRIORITY)(self._on_after_tool)
        hub.on(RunCompleted, priority=_PRIORITY)(self._on_terminal)
        hub.on(RunPaused, priority=_PRIORITY)(self._on_terminal)
        hub.on(RunCancelled, priority=_PRIORITY)(self._on_terminal)
        hub.on(RunFailed, priority=_PRIORITY)(self._on_terminal)

    async def _on_run_started(self, event: RunStarted, deps: HandlerDeps) -> None:
        self._store.save_state(event.run_id, event.state, status=RunStatus.RUNNING)

    async def _on_after_model(self, event: RunAfterModel, deps: HandlerDeps) -> None:
        self._store.save_state(event.run_id, event.state)

    async def _on_after_tool(self, event: RunAfterTool, deps: HandlerDeps) -> None:
        self._store.save_state(event.run_id, event.state)

    async def _on_terminal(self, event: TerminalRunEvent, deps: HandlerDeps) -> None:
        self._store.save_state(
            event.run_id,
            event.state,
            status=event.result.status,
            final_message=event.result.final_message,
        )
