from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from milky_frog.domain import Message, MessageRole, ModelRequest
from milky_frog.handlers.context import BudgetedRequest, HandlerContext
from milky_frog.handlers.dispatcher import BaseHandler, EventDispatcher

if TYPE_CHECKING:
    from milky_frog.handlers.events import (
        RunAfterModel,
        RunBeforeModel,
        RunBeforeResume,
        RunStarted,
    )
    from milky_frog.harness.tokens import TokenCounter

logger = logging.getLogger(__name__)


@dataclass
class BudgetConfig:
    """Configuration for token budgeting."""

    context_window: int
    output_reserve: int
    safety_margin: int


class OnlineAffineCalibrator:
    """Incremental affine regressor: ``real ≈ intercept_ + coef_ * raw``.

    The API mirrors scikit-learn's incremental estimators — ``partial_fit`` to
    learn from one ``(raw, real)`` measurement and ``predict`` to estimate —
    and fitted parameters use the trailing-underscore convention (``coef_``,
    ``intercept_``).

    It is an ordinary least-squares line fit *online*, keeping no sample
    history: each measurement only updates four exponential moving averages of
    the moments ``E[x], E[y], E[x²], E[xy]`` (``x`` the raw estimate, ``y`` the
    real token count), and the coefficients are read straight off them::

        coef_      = Cov(x, y) / Var(x)
        intercept_ = E[y] - coef_ * E[x]

    with ``Var(x) = E[x²] - E[x]²`` and ``Cov(x, y) = E[xy] - E[x]·E[y]``. The
    EMA weight ``alpha`` makes recent measurements count more, so the fit tracks
    provider drift while a single odd request only nudges it.

    The constructor hyperparameters are safety bounds, not part of the
    regression: ``min_coef`` floors a prediction at ``min_coef * raw`` because
    under-prediction is the harmful direction (it overflows the context
    window), ``max_coef`` caps the slope, ``max_intercept`` caps the fixed
    overhead, and ``min_variance`` is the spread below which the samples are too
    colinear to fit a slope — there the fit degrades to a pure proportional
    ratio ``real / raw`` with no intercept.
    """

    def __init__(
        self,
        *,
        alpha: float = 0.3,
        min_coef: float = 0.25,
        max_coef: float = 8.0,
        min_variance: float = 1.0,
        max_intercept: float | None = None,
    ) -> None:
        self.alpha = alpha
        self.min_coef = min_coef
        self.max_coef = max_coef
        self.min_variance = min_variance
        self.max_intercept = max_intercept
        self._fitted = False
        # EMA moments of (x=raw, y=real).
        self._mx = 0.0
        self._my = 0.0
        self._mxx = 0.0
        self._mxy = 0.0

    @property
    def fitted(self) -> bool:
        """Whether at least one measurement has been observed."""
        return self._fitted

    def partial_fit(self, raw: float, real: float) -> None:
        """Fold one ``(raw estimate, real token count)`` measurement into the fit.

        Non-positive measurements are ignored (no usage to anchor to). The first
        valid sample seeds the moments exactly; later ones blend in via the EMA.
        """
        if raw <= 0 or real <= 0:
            return
        if not self._fitted:
            self._mx, self._my = raw, real
            self._mxx, self._mxy = raw * raw, raw * real
            self._fitted = True
            return
        a = self.alpha
        self._mx += a * (raw - self._mx)
        self._my += a * (real - self._my)
        self._mxx += a * (raw * raw - self._mxx)
        self._mxy += a * (raw * real - self._mxy)

    @property
    def coef_(self) -> float:
        """Slope: real tokens per raw token, bounded to ``[0, max_coef]``."""
        if not self._fitted or self._mx <= 0:
            return 1.0
        variance = self._mxx - self._mx * self._mx
        if variance > self.min_variance:
            slope = (self._mxy - self._mx * self._my) / variance
        else:
            slope = self._my / self._mx  # too colinear: proportional ratio
        return min(self.max_coef, max(0.0, slope))

    @property
    def intercept_(self) -> float:
        """Fixed request overhead, anchored through the data centroid."""
        if not self._fitted or self._mx <= 0:
            return 0.0
        variance = self._mxx - self._mx * self._mx
        if variance <= self.min_variance:
            return 0.0  # proportional fallback carries no intercept
        value = max(0.0, self._my - self.coef_ * self._mx)
        if self.max_intercept is not None:
            value = min(value, self.max_intercept)
        return value

    def predict(self, raw: float) -> float:
        """Calibrated estimate for one raw count, floored against under-prediction."""
        if not self._fitted or raw <= 0:
            return float(raw)  # cold start: identity (calibration = 1.0)
        predicted = self.intercept_ + self.coef_ * raw
        return max(self.min_coef * raw, predicted)


class BudgetHandler(BaseHandler):
    """Trims ModelRequest to a token budget before each model call.

    Subscribes to ``RunStarted`` and ``RunBeforeResume`` to load the workspace
    budget config (a fresh process resuming a Run never sees ``RunStarted``, so
    both seams must initialize), and to ``RunBeforeModel`` to trim.

    Trimming keeps the system prompt and tool schemas (non-negotiable) plus the
    most recent contiguous tail of the conversation that fits the budget. Tail
    contiguity preserves chronological order and keeps every assistant
    ``tool_calls`` message together with its ``tool`` results, so the provider
    never sees an orphaned tool result or reordered history.

    Budget: ``input_budget = context_window - output_reserve - safety_margin``.

    The local token estimate is only approximate — it can't see provider-side
    function-calling scaffolding and uses a generic char-ratio tokenizer. So the
    handler anchors to reality with an :class:`OnlineAffineCalibrator`: each
    request actually sent yields one ``(raw estimate, reported input_tokens)``
    measurement, which ``partial_fit`` folds into an online affine regression
    ``real ≈ intercept_ + coef_ * raw``. Every budget decision goes through the
    calibrator's ``predict``.
    """

    # Predictions are clamped to this band around the raw estimate.
    _MIN_CALIBRATION = 0.25
    _MAX_CALIBRATION = 8.0
    # EMA weight for each new measurement; smooths noise while tracking drift.
    _MOMENT_ALPHA = 0.3
    # Minimum spread in raw estimates before a slope can be fit; below this the
    # samples are too colinear and we fall back to the proportional ratio.
    _MIN_VARIANCE = 1.0

    def __init__(self) -> None:
        self._counter: TokenCounter | None = None
        self._config: BudgetConfig | None = None
        self._input_budget = 0
        self._calibrator = OnlineAffineCalibrator(
            alpha=self._MOMENT_ALPHA,
            min_coef=self._MIN_CALIBRATION,
            max_coef=self._MAX_CALIBRATION,
            min_variance=self._MIN_VARIANCE,
        )

    def register(self, registry: EventDispatcher) -> None:
        from milky_frog.handlers.events import (
            RunAfterModel,
            RunBeforeModel,
            RunBeforeResume,
            RunStarted,
        )

        registry.on(RunStarted)(self._on_run_started)
        registry.on(RunBeforeResume)(self._on_run_before_resume)
        registry.on(RunBeforeModel)(self._on_run_before_model)
        registry.on(RunAfterModel)(self._on_run_after_model)

    async def _on_run_started(self, event: RunStarted, ctx: HandlerContext) -> None:
        """Initialize the counter and budget config when a fresh run starts."""
        self._init_for_workspace(event.state.workspace)

    async def _on_run_before_resume(self, event: RunBeforeResume, ctx: HandlerContext) -> None:
        """Initialize the counter and budget config when a Run is resumed."""
        self._init_for_workspace(event.workspace)

    def _init_for_workspace(self, workspace: Path) -> None:
        from milky_frog.harness.tokens import ApproxCharCounter
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
        self._calibrator.max_intercept = float(project_cfg.context_window)

    async def _on_run_before_model(
        self, event: RunBeforeModel, ctx: HandlerContext
    ) -> BudgetedRequest | None:
        """Trim the request if it exceeds the input budget."""
        if self._counter is None or self._config is None:
            return None

        request = event.request
        if self._count_request_tokens(request) <= self._input_budget:
            return None

        trimmed = self._trim_request(request)
        if trimmed != request:
            return BudgetedRequest(request=trimmed)
        return None

    async def _on_run_after_model(self, event: RunAfterModel, ctx: HandlerContext) -> None:
        """Recalibrate the estimate against the provider's reported input tokens.

        ``event.request`` is the request actually sent (post-trim), so the pair
        (our raw estimate, the reported ``input_tokens``) is one ground-truth
        measurement to ``partial_fit``.
        """
        usage = event.response.usage
        if self._counter is None or not usage.recorded or usage.input_tokens <= 0:
            return
        self._calibrator.partial_fit(self._raw_count(event.request), float(usage.input_tokens))

    def _count_request_tokens(self, request: ModelRequest) -> int:
        """Calibrated token estimate used for every budget decision."""
        return round(self._calibrator.predict(self._raw_count(request)))

    def _raw_count(self, request: ModelRequest) -> int:
        """Uncalibrated estimate: messages (incl. tool calls) + tool schemas."""
        if self._counter is None:
            return 0
        message_dicts = [self._message_count_dict(m) for m in request.messages]
        return self._counter.count_messages(message_dicts) + self._counter.count_tool_schemas(
            request.tools
        )

    @staticmethod
    def _message_count_dict(message: Message) -> dict[str, str]:
        """Render a message for counting, folding tool-call args into the content.

        Assistant ``tool_calls`` are part of the serialized request, so their
        arguments must be counted; ``count_messages`` only sees role + content.
        """
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

        return ModelRequest((*system_msgs, *kept), request.tools)
