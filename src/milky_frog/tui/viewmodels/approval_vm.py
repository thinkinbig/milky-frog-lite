from __future__ import annotations

from rich.text import Text
from textual.widgets import Input

from milky_frog.domain import ApprovalDecision, ApprovalVerdict
from milky_frog.tui.messages import ApprovalRequired, PendingApproval
from milky_frog.tui.viewmodels.protocols import TuiHost
from milky_frog.tui.widgets.approval import ApprovalPrompt


class ApprovalViewModel:
    """Manages the approval flow state machine: menu display, text input fallback,
    denial reason collection, and verdict dispatch."""

    def __init__(self, app: TuiHost) -> None:
        self._app = app
        self._pending: ApprovalRequired | None = None
        self._position = 0
        self._verdicts: dict[str, ApprovalVerdict] = {}
        self._widget: ApprovalPrompt | None = None
        self._deny_reason_mode: bool = False

    @property
    def is_pending(self) -> bool:
        return self._pending is not None

    @property
    def deny_reason_mode(self) -> bool:
        return self._deny_reason_mode

    @property
    def has_menu(self) -> bool:
        return self._widget is not None and not self._deny_reason_mode

    def begin(self, event: ApprovalRequired) -> None:
        """Start collecting decisions for every Tool call in a pending batch."""
        self._app._conv.close_phase()
        self._app.session.pending_approval = event.run_id
        self._pending = event
        self._position = 0
        self._verdicts = {}
        self._deny_reason_mode = False
        # Before ``_show_current``: an empty batch dispatches straight through to
        # ``_start_approvals``, which sets ``busy`` itself. Clearing it afterwards
        # would leave the App idle while its approval worker is still running.
        self._app.session.busy = False
        self._show_current()

    def _current(self) -> PendingApproval | None:
        event = self._pending
        if event is None or self._position >= len(event.approvals):
            return None
        return event.approvals[self._position]

    def _skip_decided(self) -> None:
        """Advance the cursor past calls that already hold a verdict.

        ``allow_tool`` / ``allow_all`` decide calls ahead of the cursor, so the
        next call to prompt for is the next *undecided* one — never simply the
        next one. Re-prompting a decided call would let a later answer overwrite
        the always-allow the user just set.
        """
        event = self._pending
        if event is None:
            return
        while self._position < len(event.approvals):
            if event.approvals[self._position].call_id not in self._verdicts:
                break
            self._position += 1

    def _show_current(self) -> None:
        self._skip_decided()
        current = self._current()
        event = self._pending
        if current is None or event is None:
            self._dispatch_batch()
            return
        if self._widget is not None:
            self._widget.remove()
        prompt = ApprovalPrompt(
            tool_name=current.tool_name,
            reason=current.reason,
            position=self._position + 1,
            total=len(event.approvals),
        )
        self._widget = prompt
        self._app._conversation().mount(prompt)
        self._app._scroll_end()
        prompt_input = self._app.query_one("#prompt-input", Input)
        prompt_input.disabled = True
        prompt_input.placeholder = "Type a task and press Enter..."

    def _record(self, verdict: ApprovalVerdict) -> None:
        current = self._current()
        if current is None:
            return
        self._verdicts[current.call_id] = verdict
        self._deny_reason_mode = False
        # No manual advance: ``_show_current`` skips every decided call, and the
        # one just recorded is now one of them.
        self._show_current()

    def _dispatch_batch(self) -> None:
        event = self._pending
        if event is None:
            return
        run_id = event.run_id
        verdicts = dict(self._verdicts)
        self._clear()
        self._app._start_approvals(run_id, verdicts)

    def handle_option(self, action: str) -> None:
        """Apply the highlighted approval choice."""
        if self._pending is None:
            return
        if action == "deny_reason":
            self._begin_deny_reason()
            return
        self._apply_action(action)

    def _begin_deny_reason(self) -> None:
        self._deny_reason_mode = True
        if self._widget is not None:
            self._widget.remove()
            self._widget = None
        self._app._append(
            Text("  Type why you're denying, then press Enter.", style="bold yellow"),
            spaced=False,
        )
        prompt_input = self._app.query_one("#prompt-input", Input)
        prompt_input.disabled = False
        prompt_input.placeholder = "Reason for denial…"
        prompt_input.focus()

    def handle_text_input(self, text: str) -> bool:
        """Parse typed approval shorthand. Returns True if input was consumed."""
        if self._pending is None:
            return False

        if self._deny_reason_mode:
            reason = text.strip()
            if not reason:
                self._app._append(
                    Text("  Please enter a reason, or Esc to cancel.", style="bold yellow"),
                    spaced=False,
                )
                return True
            self._record(ApprovalVerdict(ApprovalDecision.DENY, denial_reason=reason))
            return True

        verdict = self._parse(text)
        if verdict is None:
            self._app._append(
                Text(
                    "  Use ↑/↓ and Enter on the menu, or type: "
                    "y / n / n because <reason> / always / always all",
                    style="bold yellow",
                ),
                spaced=False,
            )
            return True

        if isinstance(verdict, str):
            self._apply_action(verdict)
        else:
            self._record(verdict)
        return True

    def _clear(self) -> None:
        self._pending = None
        self._position = 0
        self._verdicts = {}
        self._app.session.pending_approval = None
        self._deny_reason_mode = False
        if self._widget is not None:
            self._widget.remove()
            self._widget = None
        prompt_input = self._app.query_one("#prompt-input", Input)
        prompt_input.placeholder = "Type a task and press Enter..."

    def _apply_action(self, action: str) -> None:
        event = self._pending
        current = self._current()
        if event is None or current is None:
            return

        if action == "approve":
            self._record(ApprovalVerdict(ApprovalDecision.APPROVE))
        elif action == "deny":
            self._record(ApprovalVerdict(ApprovalDecision.DENY))
        elif action == "allow_tool":
            if current.tool_name:
                self._app.session.policy.allow(current.tool_name)
            for approval in event.approvals[self._position :]:
                if approval.tool_name == current.tool_name:
                    self._verdicts[approval.call_id] = ApprovalVerdict(ApprovalDecision.APPROVE)
            self._show_current()
        elif action == "allow_all":
            self._app.session.policy.auto_approve()
            for approval in event.approvals[self._position :]:
                self._verdicts[approval.call_id] = ApprovalVerdict(ApprovalDecision.APPROVE)
            self._show_current()

    @staticmethod
    def _parse(text: str) -> ApprovalVerdict | str | None:
        lowered = text.strip().lower()

        if lowered in ("y", "yes", "approve"):
            return ApprovalVerdict(ApprovalDecision.APPROVE)
        if lowered in ("n", "no", "deny"):
            return ApprovalVerdict(ApprovalDecision.DENY)

        for prefix in ("no because ", "n because "):
            if lowered.startswith(prefix):
                reason = text.strip()[len(prefix) :].strip()
                return ApprovalVerdict(ApprovalDecision.DENY, denial_reason=reason)

        if lowered in ("always", "always allow"):
            return "allow_tool"
        if lowered in (
            "always all",
            "always_all",
            "alwaysall",
            "don't ask again",
            "dont ask again",
        ):
            return "allow_all"

        return None
