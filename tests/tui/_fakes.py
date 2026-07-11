"""Fake widget, session, and host doubles for ``tests/tui/`` VM tests.

``ConversationViewModel`` and friends talk to their host through the
``TuiHost`` Protocol. The testing strategy is to instantiate a fake host,
exercise the VM's public methods, then assert against what the host records.
We do NOT spin up Textual; these tests verify the VM's render decisions, not
the widget tree.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from rich.console import RenderableType

from milky_frog.tui.viewmodels.conversation_vm import ConversationViewModel

# ── Widget fakes (Textual widgets reduced to the surfaces VMs touch) ─


class FakeTimer:
    """Stand-in for ``textual.timer.Timer``; records that ``stop`` was called."""

    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class FakeStatic:
    """Stand-in for ``textual.widgets.Static``."""

    def __init__(self) -> None:
        self.updates: list[RenderableType] = []
        self.removed = False

    def update(self, renderable: RenderableType) -> None:
        self.updates.append(renderable)

    def remove(self) -> None:
        self.removed = True


class FakeInput:
    """Stand-in for ``textual.widgets.Input``."""

    def __init__(self) -> None:
        self.disabled = False
        self.placeholder = ""
        self.value = ""
        self.cursor_position = 0
        self.focus_calls = 0

    def focus(self) -> None:
        self.focus_calls += 1


class FakeVerticalScroll:
    """Stand-in for the ``#conversation`` ``VerticalScroll``."""

    def __init__(self) -> None:
        self.mounted_widgets: list[Any] = []
        self.removed_children: list[Any] = []
        self.scroll_end_calls = 0

    def mount(self, widget: Any) -> None:
        self.mounted_widgets.append(widget)

    def remove_children(self) -> None:
        self.removed_children.extend(self.mounted_widgets)
        self.mounted_widgets.clear()

    def scroll_end(self, *, animate: bool = True) -> None:
        self.scroll_end_calls += 1


class FakeApprovalPrompt:
    """Stand-in for the real ``ApprovalPrompt`` widget.

    The real class extends ``textual.containers.Vertical`` and, when removed,
    walks up the Textual DOM — which requires an active App. Tests cannot
    spin up a real App just for VM tests, so we swap it with this duck-typed
    shim that records lifecycle calls only.
    """

    def __init__(self, *, tool_name: str, reason: str, position: int, total: int) -> None:
        self.tool_name = tool_name
        self.reason = reason
        self.position = position
        self.total = total
        self.removed = False
        self.options = []  # actions the menu would offer

    def remove(self) -> None:
        self.removed = True


class FakeSkillPicker:
    """Stand-in for ``SkillPicker`` — same rationale as ``FakeApprovalPrompt``."""

    def __init__(self, entries: tuple, active: frozenset[str]) -> None:
        self.entries = entries
        self.active = active
        self.removed = False

    def action_dismiss(self) -> None:
        self.removed = True

    def remove(self) -> None:
        self.removed = True


class FakePolicy:
    """Stand-in for ``session.policy`` (``ToolPolicy``)."""

    def __init__(self) -> None:
        self.allowed: list[str] = []
        self.auto_approve_calls = 0

    def allow(self, tool_name: str) -> None:
        self.allowed.append(tool_name)

    def auto_approve(self) -> None:
        self.auto_approve_calls += 1


class FakeSession:
    """Stand-in for ``AgentSession`` covering the surface VMs touch."""

    def __init__(self) -> None:
        self.busy = False
        self.pending_approval: str | None = None
        self.policy = FakePolicy()
        self.skills_home: Path = Path("/tmp/skills")
        self.run_id: str | None = None

    def attach_worker(self, cancel: Callable[[], None]) -> None:
        del cancel


# ── The FakeTuiHost ────────────────────────────────────────────────


class FakeTuiHost:
    """Implements the ``TuiHost`` Protocol surface used by VMs."""

    def __init__(self) -> None:
        self.session = FakeSession()
        self._conversation_vm = ConversationViewModel(self)  # type: ignore[arg-type]
        self._conversation_widget = FakeVerticalScroll()
        self._prompt_input = FakeInput()
        self.appended: list[tuple[RenderableType, bool]] = []
        self.scroll_calls = 0
        self.intervals: list[tuple[float, Callable[[], object]]] = []
        self.started_approvals: list[tuple[str, dict[str, object]]] = []

    # TuiHost Protocol surface ─────────────────────────────────────

    @property
    def _conv(self) -> ConversationViewModel:
        return self._conversation_vm

    def _append(self, renderable: RenderableType, *, spaced: bool = True) -> FakeStatic:
        static = FakeStatic()
        self.appended.append((renderable, spaced))
        return static

    def _scroll_end(self) -> None:
        self.scroll_calls += 1

    def _conversation(self) -> FakeVerticalScroll:
        return self._conversation_widget

    def set_interval(self, interval: float, callback: Callable[[], object]) -> FakeTimer:
        self.intervals.append((interval, callback))
        return FakeTimer()

    def query_one(self, selector: str, expect_type: type) -> Any:
        # The VMs ask for the real ``textual.widgets.Input`` / ``VerticalScroll``
        # types, but our fakes are deliberately duck-type-compatible. Match by
        # qualname so we stay decoupled from Textual internals.
        module = (expect_type.__module__ or "") + "." + expect_type.__qualname__
        if module.endswith(".Input") or expect_type.__name__ == "Input":
            return self._prompt_input
        if module.endswith(".VerticalScroll") or expect_type.__name__ == "VerticalScroll":
            return self._conversation_widget
        raise LookupError(f"No widget matches {selector!r}")

    def _start_approvals(self, run_id: str, verdicts: dict[str, object]) -> None:
        self.started_approvals.append((run_id, verdicts))
