from __future__ import annotations

from rich.text import Text
from textual.widgets import Input

from milky_frog.domain import ApprovalDecision, ApprovalVerdict
from milky_frog.tui.messages import ApprovalRequired
from milky_frog.tui.viewmodels.protocols import TuiHost
from milky_frog.tui.widgets.approval import ApprovalPrompt


class ApprovalViewModel:
    """Manages the approval flow state machine: menu display, text input fallback,
    denial reason collection, and verdict dispatch."""

    def __init__(self, app: TuiHost) -> None:
        self._app = app
        self._pending: ApprovalRequired | None = None
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
        """Show the approval menu for a pending tool call."""
        self._app._conv.close_phase()
        self._app.session.pending_approval = event.run_id
        self._pending = event
        self._deny_reason_mode = False

        prompt = ApprovalPrompt(tool_name=event.tool_name, reason=event.reason)
        self._widget = prompt
        self._app._conversation().mount(prompt)
        self._app._scroll_end()

        self._app.session.busy = False
        prompt_input = self._app.query_one("#prompt-input", Input)
        prompt_input.disabled = True
        prompt_input.placeholder = "Type a task and press Enter..."

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
            run_id = self._pending.run_id
            self._clear()
            self._app._start_approval(
                run_id,
                ApprovalVerdict(ApprovalDecision.DENY, denial_reason=reason),
            )
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
            run_id = self._pending.run_id
            self._clear()
            self._app._start_approval(run_id, verdict)
        return True

    def _clear(self) -> None:
        self._pending = None
        self._app.session.pending_approval = None
        self._deny_reason_mode = False
        if self._widget is not None:
            self._widget.remove()
            self._widget = None
        prompt_input = self._app.query_one("#prompt-input", Input)
        prompt_input.placeholder = "Type a task and press Enter..."

    def _apply_action(self, action: str) -> None:
        event = self._pending
        if event is None:
            return
        self._clear()

        if action == "approve":
            self._app._start_approval(event.run_id, ApprovalVerdict(ApprovalDecision.APPROVE))
        elif action == "deny":
            self._app._start_approval(event.run_id, ApprovalVerdict(ApprovalDecision.DENY))
        elif action == "allow_tool":
            if event.tool_name:
                self._app.session.policy.allow(event.tool_name)
            self._app._start_approval(event.run_id, ApprovalVerdict(ApprovalDecision.APPROVE))
        elif action == "allow_all":
            self._app.session.policy.auto_approve()
            self._app._start_approval(event.run_id, ApprovalVerdict(ApprovalDecision.APPROVE))

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
