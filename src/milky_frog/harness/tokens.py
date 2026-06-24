from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import JsonValue

from milky_frog.domain import Message, MessageRole, ModelRequest

# Per-message overhead (~4 tokens for role/formatting).
_PER_MESSAGE_OVERHEAD = 4
# Rough characters-per-token ratio for the estimator.
_CHARS_PER_TOKEN = 4

logger = logging.getLogger(__name__)


class TokenCounter(Protocol):
    """Protocol for counting tokens in model requests and responses."""

    def count_text(self, text: str) -> int: ...

    def count_messages(
        self, messages: list[dict[str, str]] | tuple[dict[str, str], ...]
    ) -> int: ...

    def count_tool_schemas(self, schemas: tuple[dict[str, JsonValue], ...]) -> int: ...


class ApproxCharCounter:
    """Provider-agnostic token estimate (~4 chars per token).

    Used for budget trimming across OpenAI-compatible providers where the
    exact tokenizer is unknown. Workspace ``safety_margin`` (default 32k on a
    128k window) absorbs provider-side counting drift without usage calibration.
    """

    def count_text(self, text: str) -> int:
        if not text:
            return 0
        return max(1, len(text) // _CHARS_PER_TOKEN)

    def count_messages(self, messages: list[dict[str, str]] | tuple[dict[str, str], ...]) -> int:
        total = 0
        for message in messages:
            total += _PER_MESSAGE_OVERHEAD
            for value in message.values():
                if isinstance(value, str):
                    total += self.count_text(value)
        return total

    def count_tool_schemas(self, schemas: tuple[dict[str, JsonValue], ...]) -> int:
        return sum(self.count_text(json.dumps(schema)) for schema in schemas)


@dataclass
class BudgetConfig:
    """Token budget configuration derived from per-workspace project config."""

    context_window: int
    output_reserve: int
    safety_margin: int


class TokenBudget:
    """Trim ``ModelRequest`` to a token budget before each model call.

    ``init_for_workspace`` loads the workspace budget config (called by
    ``AgentHarness`` at the start of each Run or resume). ``trim`` applies the
    budget before sending the request using :class:`ApproxCharCounter`.

    Trimming keeps system messages and tool schemas (non-negotiable) plus the
    most recent contiguous tail of the conversation that fits the budget. Tail
    contiguity preserves chronological order and keeps every assistant
    ``tool_calls`` message together with its ``tool`` results, so the provider
    never sees an orphaned tool result or reordered history.

    Budget: ``input_budget = context_window - output_reserve - safety_margin``.
    """

    def __init__(self) -> None:
        self._counter: TokenCounter | None = None
        self._config: BudgetConfig | None = None
        self._input_budget = 0

    def init_for_workspace(self, workspace: Path) -> None:
        """Load budget configuration from the workspace project config."""
        from milky_frog.project import load_project_config

        self._counter = ApproxCharCounter()
        project_cfg = load_project_config(workspace)
        self._config = BudgetConfig(
            context_window=project_cfg.context_window,
            output_reserve=project_cfg.output_reserve,
            safety_margin=project_cfg.safety_margin,
        )
        self._input_budget = (
            project_cfg.context_window - project_cfg.output_reserve - project_cfg.safety_margin
        )

    def trim(self, request: ModelRequest) -> ModelRequest:
        """Return a trimmed request if it exceeds the budget, otherwise the original."""
        if self._counter is None or self._config is None:
            return request
        if self._count_request_tokens(request) <= self._input_budget:
            return request
        return self._trim_request(request)

    def _count_request_tokens(self, request: ModelRequest) -> int:
        """Provider-agnostic token estimate used for every budget decision."""
        if self._counter is None:
            return 0
        message_dicts = [self._message_count_dict(m) for m in request.messages]
        return self._counter.count_messages(message_dicts) + self._counter.count_tool_schemas(
            request.tools
        )

    @staticmethod
    def _message_count_dict(message: Message) -> dict[str, str]:
        """Render a message for counting, folding tool-call args into the content."""
        content = message.content
        if message.tool_calls:
            content += json.dumps(
                [{"name": c.name, "arguments": c.arguments} for c in message.tool_calls]
            )
        return {"role": message.role.value, "content": content}

    def _trim_request(self, request: ModelRequest) -> ModelRequest:
        """Drop oldest messages so the request fits, preserving order and pairing.

        Strategy:
        1. Keep all system messages plus the tool schemas (non-negotiable).
        2. Grow a contiguous suffix of the most recent non-system messages,
           oldest-boundary-first, until the next older message would overflow.
        3. Drop any leading ``tool`` results left orphaned at the boundary.
        """
        messages = request.messages
        if not messages:
            return request

        system_msgs = tuple(m for m in messages if m.role == MessageRole.SYSTEM)
        rest = [m for m in messages if m.role != MessageRole.SYSTEM]

        base_tokens = self._count_request_tokens(ModelRequest(system_msgs, request.tools))
        if base_tokens > self._input_budget:
            logger.warning(
                "system prompt and tool schemas (%d tokens) exceed the input budget (%d); "
                "cannot trim further, sending request unmodified",
                base_tokens,
                self._input_budget,
            )
            return request

        # Walk the boundary from newest to oldest; the suffix is monotonic, so
        # once it overflows every older boundary overflows too.
        start = len(rest)
        for i in range(len(rest) - 1, -1, -1):
            candidate = ModelRequest((*system_msgs, *rest[i:]), request.tools)
            if self._count_request_tokens(candidate) > self._input_budget:
                break
            start = i

        kept = rest[start:]
        while kept and kept[0].role == MessageRole.TOOL:
            kept = kept[1:]

        return ModelRequest((*system_msgs, *kept), request.tools, run_id=request.run_id)
