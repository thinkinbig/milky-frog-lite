from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeVar

from rich.console import RenderableType
from textual.containers import VerticalScroll
from textual.message import Message
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static

from milky_frog.app.session import AgentSession
from milky_frog.domain import ApprovalVerdict


class _PhaseCloser(Protocol):
    """Narrow view of ConversationViewModel — only what ApprovalViewModel needs."""

    def close_phase(self) -> None: ...


_W = TypeVar("_W", bound=Widget)


class TuiHost(Protocol):
    """Surface area of ``MilkyFrogApp`` that ViewModels depend on.

    ViewModels accept this protocol instead of ``object`` so pyrefly can
    verify every call without ``type: ignore[attr-defined]``.
    """

    @property
    def session(self) -> AgentSession: ...

    @property
    def _conv(self) -> _PhaseCloser: ...

    def _append(self, renderable: RenderableType, *, spaced: bool = True) -> Static: ...

    def _scroll_end(self) -> None: ...

    def _conversation(self) -> VerticalScroll: ...

    def set_interval(self, interval: float, callback: Callable[[], object]) -> Timer: ...

    def query_one(self, selector: str, expect_type: type[_W]) -> _W: ...

    def _start_approval(self, run_id: str, verdict: ApprovalVerdict) -> None: ...

    def post_message(self, message: Message) -> bool: ...
