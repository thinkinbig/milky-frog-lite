from __future__ import annotations

from milky_frog.domain import Message, MessageRole, RunState
from milky_frog.harness.prompt import system_prompt
from milky_frog.harness.prompt_context import ContextLoader


class ContextManager:
    """Assembles the message list sent to the model from a ``RunState``.

    The durable transcript (``RunState.messages``) holds only the conversation —
    user / assistant / tool turns. The system prompt is **not** stored there; it
    is rebuilt here on every model call so it always reflects the *current*
    Workspace context (an updated ``CLAUDE.md``, today's date) rather than a
    value frozen at Run start. This keeps the snapshot smaller and lets resume
    pick up fresh project instructions.
    """

    def __init__(self, context_loader: ContextLoader | None = None) -> None:
        self._context_loader = context_loader

    def assemble(self, state: RunState) -> tuple[Message, ...]:
        """Return the message list to send to the model for this turn.

        When the state carries a compaction summary, the summarized prefix is
        replaced by a single summary message; the recent tail is sent verbatim.
        """
        return (self._system_message(state), *self._body(state))

    @staticmethod
    def _body(state: RunState) -> tuple[Message, ...]:
        compaction = state.compaction
        if compaction is None:
            return state.messages
        summary = Message(
            MessageRole.USER,
            f"Summary of the earlier conversation (older messages omitted):\n\n"
            f"{compaction.summary}",
        )
        return (summary, *state.messages[compaction.through_index :])

    def _system_message(self, state: RunState) -> Message:
        extra: list[str] = []
        if self._context_loader is not None:
            section = self._context_loader(state.workspace)
            if section is not None:
                extra.append(section)
        extra.extend(state.run_extra)
        return Message(MessageRole.SYSTEM, system_prompt(state.workspace, tuple(extra)))
