from __future__ import annotations

import contextlib

from milky_frog.core.handlers import Compacted, HandlerContext
from milky_frog.domain import (
    CompactionState,
    Message,
    MessageRole,
    ModelRequest,
    RunState,
    StreamDone,
)
from milky_frog.events.events import RunBeforeModel
from milky_frog.events.hub import BaseHandler, EventHub
from milky_frog.models import Model
from milky_frog.tokens import TokenCounter

_SUMMARY_INSTRUCTIONS = (
    "You are compressing a coding agent's working transcript so the task can continue with "
    "less context. Write a dense summary that preserves: the user's goal, decisions made, "
    "facts discovered, file paths touched, and any open or next steps. Omit chit-chat. "
    "Output only the summary."
)


class CompactionHandler(BaseHandler):
    """Summarizes the oldest rounds when the transcript grows past a token budget.

    A ``RunBeforeModel`` control handler (route B): when ``state.messages`` exceeds
    ``trigger_tokens``, it summarizes everything before a recent-round boundary and
    returns a ``Compacted``. The loop folds it into ``RunState.compaction``; the
    original messages are never deleted (the snapshot stays the full truth) — only
    the request sent to the model uses the summary in their place.
    """

    def __init__(
        self,
        model: Model,
        counter: TokenCounter,
        *,
        trigger_tokens: int,
        keep_recent_rounds: int = 3,
    ) -> None:
        self._model = model
        self._counter = counter
        self._trigger_tokens = trigger_tokens
        self._keep_recent_rounds = max(1, keep_recent_rounds)

    def register(self, hub: EventHub) -> None:
        hub.on(RunBeforeModel)(self._on_before_model)

    async def _on_before_model(
        self, event: RunBeforeModel, ctx: HandlerContext | None = None
    ) -> Compacted | None:
        state = event.state
        if not self._over_budget(state):
            return None
        cutoff = self._cutoff(state)
        if cutoff is None:
            return None
        summary = await self._summarize(state, cutoff)
        if not summary:
            return None
        return Compacted(CompactionState(summary=summary, through_index=cutoff))

    def _over_budget(self, state: RunState) -> bool:
        counts = [{"role": m.role.value, "content": m.content} for m in state.messages]
        return self._counter.count_messages(counts) > self._trigger_tokens

    def _cutoff(self, state: RunState) -> int | None:
        """Index to summarize through — a recent-round (user-message) boundary.

        Cutting on a user-message boundary keeps the tail well-formed: it never
        starts with an orphaned tool result whose tool call was summarized away.
        """
        starts = [i for i, message in enumerate(state.messages) if message.role is MessageRole.USER]
        if len(starts) <= self._keep_recent_rounds:
            return None
        cutoff = starts[-self._keep_recent_rounds]
        already = state.compaction.through_index if state.compaction is not None else 0
        if cutoff <= already:
            return None  # nothing new to summarize since last time
        return cutoff

    async def _summarize(self, state: RunState, cutoff: int) -> str:
        parts: list[str] = []
        if state.compaction is not None:
            parts.append(f"Summary so far:\n{state.compaction.summary}")
        parts.extend(
            f"{message.role.value}: {message.content}" for message in state.messages[:cutoff]
        )
        request = ModelRequest(
            messages=(
                Message(MessageRole.SYSTEM, _SUMMARY_INSTRUCTIONS),
                Message(MessageRole.USER, "\n\n".join(parts)),
            ),
            tools=(),
            run_id=state.run_id,
        )
        async with contextlib.aclosing(self._model.stream(request)) as stream:
            async for chunk in stream:
                if isinstance(chunk, StreamDone):
                    return chunk.response.content
        return ""
