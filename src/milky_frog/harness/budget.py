from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from milky_frog.domain import MessageRole, ModelRequest
from milky_frog.tokens import ApproxCharCounter, TokenCounter, count_request_tokens

logger = logging.getLogger(__name__)


@dataclass
class BudgetConfig:
    """Token budget configuration derived from per-workspace project config."""

    context_window: int
    output_reserve: int
    safety_margin: int


class TokenBudget:
    """Trim ``ModelRequest`` to a token budget before each model call.

    Budget::

        input_budget   = context_window - output_reserve - safety_margin
        request_tokens = count_messages(messages) + count_tool_schemas(tools)
        trim  iff  request_tokens > input_budget

    Counts come from the injected :class:`TokenCounter` (provider-specific when
    available, else :class:`ApproxCharCounter`); ``init_for_workspace`` loads the
    config, called by ``AgentHarness`` at the start of each Run or resume.

    Trimming keeps system messages and tool schemas (non-negotiable) plus the
    longest recent contiguous tail with ``request_tokens <= input_budget``. Tail
    contiguity preserves chronological order and keeps every assistant
    ``tool_calls`` message together with its ``tool`` results, so the provider
    never sees an orphaned tool result or reordered history.
    """

    def __init__(self, counter: TokenCounter | None = None) -> None:
        self._counter: TokenCounter | None = counter
        self._config: BudgetConfig | None = None
        self._input_budget = 0

    @property
    def counter(self) -> TokenCounter | None:
        return self._counter

    def init_for_workspace(self, workspace: Path) -> None:
        """Load budget configuration from the workspace project config."""
        from milky_frog.project import load_project_config

        if self._counter is None:
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
        return count_request_tokens(self._counter, request)

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
