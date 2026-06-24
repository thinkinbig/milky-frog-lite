from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from milky_frog.domain import Message, MessageRole, ModelRequest
from milky_frog.handlers.context import BudgetedRequest, HandlerContext
from milky_frog.handlers.dispatcher import BaseHandler, EventDispatcher

if TYPE_CHECKING:
    from milky_frog.handlers.events import RunBeforeModel, RunStarted
    from milky_frog.harness.tokens import TokenCounter


@dataclass
class BudgetConfig:
    """Configuration for token budgeting."""

    context_window: int
    output_reserve: int
    safety_margin: int


class BudgetHandler(BaseHandler):
    """Trims ModelRequest to a token budget via greedy allocation by priority.

    Subscribes to RunStarted and RunBeforeModel. On run start, loads the budget
    config for that workspace. On each model call, returns BudgetedRequest if
    the assembled request exceeds the budget.

    Sections are evicted in reverse priority order:
    - Core: system prompt, most recent assistant turn (never evicted)
    - Working: recent messages up to budget
    - Episodic: retrieved code/repo results (low priority)

    Budget allocation: input_budget = context_window - output_reserve - margin
    """

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._counter: TokenCounter | None = None
        self._config: BudgetConfig | None = None
        self._input_budget = 0

    def register(self, registry: EventDispatcher) -> None:
        from milky_frog.handlers.events import RunBeforeModel, RunStarted

        registry.on(RunStarted)(self._on_run_started)
        registry.on(RunBeforeModel)(self._on_run_before_model)

    async def _on_run_started(
        self, event: RunStarted, ctx: HandlerContext
    ) -> BudgetedRequest | None:
        """Initialize token counter and config when a run starts."""
        from milky_frog.harness.tokens import TiktokenCounter
        from milky_frog.project import load_project_config

        self._counter = TiktokenCounter(self._model_name)
        project_cfg = load_project_config(event.state.workspace)
        self._config = BudgetConfig(
            context_window=project_cfg.context_window,
            output_reserve=project_cfg.output_reserve,
            safety_margin=project_cfg.safety_margin,
        )
        self._input_budget = (
            project_cfg.context_window
            - project_cfg.output_reserve
            - project_cfg.safety_margin
        )
        return None

    async def _on_run_before_model(
        self, event: RunBeforeModel, ctx: HandlerContext
    ) -> BudgetedRequest | None:
        """Trim request if it exceeds the input budget."""
        if self._counter is None or self._config is None:
            return None

        request = event.request
        current_tokens = self._count_request_tokens(request)

        if current_tokens <= self._input_budget:
            return None

        trimmed_request = self._trim_request(request, current_tokens)
        if trimmed_request != request:
            return BudgetedRequest(request=trimmed_request)
        return None

    def _count_request_tokens(self, request: ModelRequest) -> int:
        """Count all tokens in the request: messages + tools + format overhead."""
        if self._counter is None:
            return 0
        message_dicts = [
            {"role": m.role.value, "content": m.content} for m in request.messages
        ]
        message_tokens = self._counter.count_messages(message_dicts)
        tool_tokens = self._counter.count_tool_schemas(request.tools)
        return message_tokens + tool_tokens

    def _trim_request(self, request: ModelRequest, current_tokens: int) -> ModelRequest:
        """Trim messages to fit budget, keeping core messages and evicting low-priority.

        Strategy:
        1. Keep system prompt (if present)
        2. Keep most recent assistant message + following tool results (working memory)
        3. Evict messages from middle in FIFO order until under budget
        """
        if not request.messages:
            return request

        messages = list(request.messages)
        system_msgs: list[Message] = []
        assistant_msgs: list[Message] = []
        tool_result_msgs: list[Message] = []
        other_msgs: list[Message] = []

        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                system_msgs.append(msg)
            elif msg.role == MessageRole.ASSISTANT:
                assistant_msgs.append(msg)
            elif msg.role == MessageRole.TOOL:
                tool_result_msgs.append(msg)
            else:
                other_msgs.append(msg)

        truncated = tuple(system_msgs)
        tokens_in_truncated = self._count_request_tokens(
            ModelRequest(truncated, request.tools)
        )

        if tokens_in_truncated > self._input_budget:
            return request

        if assistant_msgs:
            last_assistant = assistant_msgs[-1]
            following_tool_results: list[Message] = []
            found_last_assistant = False
            for msg in messages:
                if msg is last_assistant:
                    found_last_assistant = True
                elif found_last_assistant and msg.role == MessageRole.TOOL:
                    following_tool_results.append(msg)
                elif found_last_assistant and msg.role != MessageRole.TOOL:
                    break

            candidate = tuple([*truncated, last_assistant, *following_tool_results])
            candidate_tokens = self._count_request_tokens(
                ModelRequest(candidate, request.tools)
            )
            if candidate_tokens <= self._input_budget:
                truncated = candidate
                tokens_in_truncated = candidate_tokens

        if tokens_in_truncated >= self._input_budget:
            return ModelRequest(truncated, request.tools)

        remaining_messages = [
            m
            for m in messages
            if m.role != MessageRole.SYSTEM and m not in truncated
        ]

        for msg in remaining_messages:
            candidate = tuple([*truncated, msg])
            candidate_tokens = self._count_request_tokens(
                ModelRequest(candidate, request.tools)
            )
            if candidate_tokens <= self._input_budget:
                truncated = candidate
                tokens_in_truncated = candidate_tokens
            elif (
                self._counter is not None
                and tokens_in_truncated + self._counter.count_text(msg.content)
                <= self._input_budget
            ):
                truncated = tuple([*truncated, msg])
                tokens_in_truncated = candidate_tokens

        return ModelRequest(truncated, request.tools)
