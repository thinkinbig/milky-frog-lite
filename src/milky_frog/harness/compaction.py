from __future__ import annotations

import contextlib
from typing import override

from milky_frog.core.handlers import HandlerDeps
from milky_frog.domain import (
    Compacted,
    CompactionState,
    Message,
    MessageRole,
    ModelRequest,
    ModelResponse,
    RunState,
    StreamDone,
)
from milky_frog.events.events import RunBeforeModel
from milky_frog.events.hub import EventHub, Handler
from milky_frog.models import Model
from milky_frog.tokens import TokenCounter, count_request_tokens, message_count_dict

_SUMMARY_INSTRUCTIONS = (
    "You are compressing a coding agent's working transcript so the task can continue with "
    "less context. Write a dense summary that preserves: the user's goal, decisions made, "
    "facts discovered, file paths touched, and any open or next steps. Omit chit-chat. "
    "Output only the summary."
)


class CompactionHandler(Handler):
    """Summarizes the oldest messages when the request grows past a token budget.

    A ``RunBeforeModel`` control handler (route B): when the assembled request
    (``event.request`` — system prompt, tool schemas, and the current transcript
    or its prior summary) exceeds ``trigger_tokens``, it summarizes everything
    before a recent-token boundary and returns a ``Compacted``. The loop folds it
    into ``RunState.compaction``; the original messages are never deleted (the
    snapshot stays the full truth) — only the request sent to the model uses the
    summary in their place.
    """

    def __init__(
        self,
        model: Model,
        counter: TokenCounter,
        *,
        trigger_tokens: int,
        keep_recent_tokens: int,
    ) -> None:
        self._model = model
        self._counter = counter
        self._trigger_tokens = trigger_tokens
        self._keep_recent_tokens = max(1, keep_recent_tokens)

    @override
    def register(self, hub: EventHub) -> None:
        hub.on(RunBeforeModel)(self._on_before_model)

    async def _on_before_model(
        self, event: RunBeforeModel, deps: HandlerDeps | None = None
    ) -> Compacted | None:
        # Measure the request actually sent (system prompt + tool schemas + the
        # already-compacted transcript), not the raw stored transcript.
        if count_request_tokens(self._counter, event.request) <= self._trigger_tokens:
            return None
        state = event.state
        cutoff = self._cutoff(state)
        if cutoff is None:
            return None
        summary = await self._summarize(state, cutoff)
        if not summary:
            return None
        return Compacted(CompactionState(summary=summary, through_index=cutoff))

    def _cutoff(self, state: RunState) -> int | None:
        """Newest index to summarize through, keeping a ``keep_recent_tokens`` tail.

        Walks from the end accumulating per-message tokens until the tail would
        exceed the keep budget, then snaps the boundary forward past any ``tool``
        messages so the tail never starts with an orphaned tool result whose call
        was summarized away. Boundaries are not restricted to user turns, so a
        single long tool loop (no new user messages) still compacts.
        """
        messages = state.messages
        already = state.compaction.through_index if state.compaction is not None else 0
        kept = 0
        cut = len(messages)
        for i in range(len(messages) - 1, already - 1, -1):
            kept += self._counter.count_text(message_count_dict(messages[i])["content"])
            if kept > self._keep_recent_tokens:
                cut = i
                break
        else:
            return None  # everything since the last compaction fits — nothing to do
        while cut < len(messages) and messages[cut].role is MessageRole.TOOL:
            cut += 1
        if not already < cut < len(messages):
            return None
        return cut

    async def _summarize(self, state: RunState, cutoff: int) -> str:
        already = state.compaction.through_index if state.compaction is not None else 0
        parts: list[str] = []
        if state.compaction is not None:
            parts.append(f"Summary so far:\n{state.compaction.summary}")
        # Only the messages newly dropped since the last compaction — the prior
        # summary already covers messages[:already].
        parts.extend(
            f"{message.role.value}: {message.content}" for message in state.messages[already:cutoff]
        )
        request = ModelRequest(
            messages=(
                Message(MessageRole.SYSTEM, _SUMMARY_INSTRUCTIONS),
                Message(MessageRole.USER, "\n\n".join(parts)),
            ),
            tools=(),
            run_id=state.run_id,
        )
        return await self._stream_final_content(request)

    @staticmethod
    async def force_compact(
        model: Model,
        counter: TokenCounter,
        state: RunState,
        *,
        keep_recent_tokens: int = 0,
    ) -> CompactionState | None:
        """Summarise **all** messages in *state* into a ``CompactionState``.

        When ``keep_recent_tokens > 0`` only messages before the keep window
        are summarised (matching the automatic path); otherwise everything is
        folded.
        """
        messages = state.messages
        threshold = keep_recent_tokens or 0
        already = state.compaction.through_index if state.compaction is not None else 0
        if not already < len(messages):
            return None
        if threshold > 0:
            kept = 0
            cut = len(messages)
            for i in range(len(messages) - 1, already - 1, -1):
                kept += counter.count_text(message_count_dict(messages[i])["content"])
                if kept > threshold:
                    cut = i
                    break
            else:
                return None
            while cut < len(messages) and messages[cut].role is MessageRole.TOOL:
                cut += 1
            if not already < cut < len(messages):
                return None
        else:
            cut = len(messages)

        parts: list[str] = []
        if state.compaction is not None:
            parts.append(f"Summary so far:\n{state.compaction.summary}")
        parts.extend(
            f"{message.role.value}: {message.content}" for message in messages[already:cut]
        )
        request = ModelRequest(
            messages=(
                Message(MessageRole.SYSTEM, _SUMMARY_INSTRUCTIONS),
                Message(MessageRole.USER, "\n\n".join(parts)),
            ),
            tools=(),
            run_id=state.run_id,
        )
        final: ModelResponse | None = None
        async with contextlib.aclosing(model.stream(request)) as stream:
            async for chunk in stream:
                if isinstance(chunk, StreamDone):
                    final = chunk.response
                    break
        if final is None or not final.content:
            return None
        return CompactionState(summary=final.content, through_index=cut)

    async def _stream_final_content(self, request: ModelRequest) -> str:
        """Run a one-shot model request and return the assembled reply text."""
        final: ModelResponse | None = None
        async with contextlib.aclosing(self._model.stream(request)) as stream:
            async for chunk in stream:
                if isinstance(chunk, StreamDone):
                    final = chunk.response
                    break
        return "" if final is None else final.content
