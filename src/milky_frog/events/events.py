from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from milky_frog.domain import (
    ModelRequest,
    ModelResponse,
    ReasoningDelta,
    RunRequest,
    RunResult,
    RunState,
    RunStatus,
    TextDelta,
    ToolCall,
    ToolResult,
)


@dataclass(frozen=True)
class BaseEvent:
    """Base type for ephemeral Harness lifecycle signals delivered via ``notify``.

    These are not Checkpoint events — they exist only for live UI and
    observability Handlers during a Run.
    """

    run_id: str


@dataclass(frozen=True)
class RunBeforeStart(BaseEvent):
    """Dispatched before the transcript is seeded and the Run is stored.

    Pure observation — handlers may inspect the request and workspace but
    cannot inject content into the system prompt via this event.  Context
    injection happens through the ``ContextLoader`` protocol injected into
    ``AgentHarness``.
    """

    request: RunRequest
    workspace: Path


@dataclass(frozen=True)
class RunBeforeResume(BaseEvent):
    """Dispatched before a Run is prepared for resumption.

    Pure observation — handlers can inspect / log the stored Run data
    before the Harness loads state, seals interrupted tools, and
    optionally appends a new user turn.  Carries the resolved workspace so
    handlers can load per-workspace config that ``RunStarted`` would otherwise
    provide (a resumed Run never sees ``RunStarted``).
    """

    prompt: str | None
    stored_status: RunStatus
    workspace: Path


@dataclass(frozen=True)
class RunStarted(BaseEvent):
    request: RunRequest
    state: RunState


@dataclass(frozen=True)
class RunBeforeModel(BaseEvent):
    request: ModelRequest


@dataclass(frozen=True)
class RunModelReasoning(BaseEvent):
    request: ModelRequest
    chunk: ReasoningDelta


@dataclass(frozen=True)
class RunModelChunk(BaseEvent):
    request: ModelRequest
    chunk: TextDelta


@dataclass(frozen=True)
class RunAfterModel(BaseEvent):
    request: ModelRequest
    response: ModelResponse
    state: RunState


@dataclass(frozen=True)
class RunBeforeTool(BaseEvent):
    """Dispatched before a tool call — pure observation.

    Return ``None`` (the default).  Authorization is enforced inline by
    ``ToolStepExecutor`` via ``ToolPolicy`` before execution begins.
    """

    call: ToolCall


@dataclass(frozen=True)
class RunAfterTool(BaseEvent):
    call: ToolCall
    result: ToolResult
    state: RunState


@dataclass(frozen=True)
class RunTurnStart(BaseEvent):
    """Emitted just before each model call in a turn."""

    model_call: int


@dataclass(frozen=True)
class RunTurnEnd(BaseEvent):
    """Emitted after every Tool in a model turn completes, before the next
    model call or terminal outcome."""

    model_call: int


@dataclass(frozen=True)
class RunCompleted(BaseEvent):
    result: RunResult
    state: RunState


@dataclass(frozen=True)
class RunPaused(BaseEvent):
    result: RunResult
    state: RunState


@dataclass(frozen=True)
class RunCancelled(BaseEvent):
    result: RunResult
    state: RunState


@dataclass(frozen=True)
class RunFailed(BaseEvent):
    result: RunResult
    state: RunState


TerminalRunEvent = RunCompleted | RunFailed | RunPaused | RunCancelled


NoticeLevel = Literal["info", "warning", "error"]


@dataclass(frozen=True)
class RunNotice(BaseEvent):
    """Ephemeral user-facing message during a Run.

    Not a lifecycle phase — examples: model connection retries, rate-limit
    warnings. Published by the Harness via ``EventHub``; UI Handlers subscribe and render.
    Not checkpointed.
    """

    message: str
    level: NoticeLevel = "info"


LIFECYCLE_EVENT_TYPES: tuple[type[BaseEvent], ...] = (
    RunBeforeStart,
    RunBeforeResume,
    RunStarted,
    RunBeforeModel,
    RunModelReasoning,
    RunModelChunk,
    RunAfterModel,
    RunBeforeTool,
    RunAfterTool,
    RunTurnStart,
    RunTurnEnd,
    RunCompleted,
    RunPaused,
    RunCancelled,
    RunFailed,
    RunNotice,
)
