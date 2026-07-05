"""Tests for ``ApprovalViewModel`` — approval menu / text-input / shorthand parse.

The real ``ApprovalPrompt`` widget extends ``textual.containers.Vertical`` and
walks the DOM on ``.remove()``; we stub it out via an autouse fixture so VM
logic can be exercised without spinning up Textual.
"""

from __future__ import annotations

import pytest

from milky_frog.domain import ApprovalDecision, ApprovalVerdict
from milky_frog.tui.messages import ApprovalRequired
from milky_frog.tui.viewmodels import approval_vm
from milky_frog.tui.viewmodels.approval_vm import ApprovalViewModel
from tests.tui._fakes import FakeApprovalPrompt, FakeTuiHost


@pytest.fixture(autouse=True)
def _stub_approval_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(approval_vm, "ApprovalPrompt", FakeApprovalPrompt)


def _vm() -> tuple[ApprovalViewModel, FakeTuiHost]:
    host = FakeTuiHost()
    return ApprovalViewModel(host), host  # type: ignore[arg-type]


def _event(
    *, run_id: str = "run-1", tool: str = "bash", reason: str = "approve?"
) -> ApprovalRequired:
    return ApprovalRequired(run_id=run_id, reason=reason, tool_name=tool)


# ── Static shorthand parser ────────────────────────────────────────


def test_parse_y_yes_approve() -> None:
    assert ApprovalViewModel._parse("y") == ApprovalVerdict(ApprovalDecision.APPROVE)
    assert ApprovalViewModel._parse("yes") == ApprovalVerdict(ApprovalDecision.APPROVE)
    assert ApprovalViewModel._parse("approve") == ApprovalVerdict(ApprovalDecision.APPROVE)


def test_parse_n_no_deny() -> None:
    assert ApprovalViewModel._parse("n") == ApprovalVerdict(ApprovalDecision.DENY)
    assert ApprovalViewModel._parse("no") == ApprovalVerdict(ApprovalDecision.DENY)
    assert ApprovalViewModel._parse("deny") == ApprovalVerdict(ApprovalDecision.DENY)


def test_parse_deny_with_reason_strips_prefix_and_whitespace() -> None:
    assert ApprovalViewModel._parse("no because bad") == ApprovalVerdict(
        ApprovalDecision.DENY, denial_reason="bad"
    )
    assert ApprovalViewModel._parse("n because  bad  ") == ApprovalVerdict(
        ApprovalDecision.DENY, denial_reason="bad"
    )


def test_parse_allow_tool_shorthand() -> None:
    assert ApprovalViewModel._parse("always") == "allow_tool"
    assert ApprovalViewModel._parse("always allow") == "allow_tool"


def test_parse_allow_all_shorthand() -> None:
    assert ApprovalViewModel._parse("always all") == "allow_all"
    assert ApprovalViewModel._parse("always_all") == "allow_all"
    assert ApprovalViewModel._parse("alwaysall") == "allow_all"
    assert ApprovalViewModel._parse("don't ask again") == "allow_all"
    assert ApprovalViewModel._parse("dont ask again") == "allow_all"


def test_parse_unknown_returns_none() -> None:
    assert ApprovalViewModel._parse("foo") is None
    assert ApprovalViewModel._parse("") is None
    assert ApprovalViewModel._parse("y maybe") is None


# ── State machine ──────────────────────────────────────────────────


def test_initial_state_is_not_pending() -> None:
    vm, _ = _vm()
    assert vm.is_pending is False
    assert vm.deny_reason_mode is False
    assert vm.has_menu is False


def test_begin_sets_pending_and_mounts_widget() -> None:
    vm, host = _vm()

    vm.begin(_event())

    assert vm.is_pending is True
    assert vm.has_menu is True
    assert host.session.pending_approval == "run-1"
    assert host.session.busy is False
    assert host._prompt_input.disabled is True
    assert len(host._conversation_widget.mounted_widgets) == 1


def test_begin_closes_existing_streaming_phase() -> None:
    vm, host = _vm()
    host._conv.on_thinking("reasoning")
    assert host._conv.phase == "thinking"

    vm.begin(_event())

    assert host._conv.phase is None


def test_clear_releases_widget_and_clears_pending() -> None:
    vm, host = _vm()
    vm.begin(_event())
    mounted = host._conversation_widget.mounted_widgets[0]

    vm._clear()

    assert vm.is_pending is False
    assert vm.has_menu is False
    assert host.session.pending_approval is None
    assert mounted.removed is True
    # ``_clear`` resets the placeholder but does NOT re-enable the input;
    # that responsibility lives outside the VM (in the App).
    assert host._prompt_input.placeholder == "Type a task and press Enter..."


# ── handle_option ───────────────────────────────────────────────────


def test_handle_option_approve_dispatches_verdict_and_clears() -> None:
    vm, host = _vm()
    vm.begin(_event())

    vm.handle_option("approve")

    assert host.started_approvals == [("run-1", ApprovalVerdict(ApprovalDecision.APPROVE))]
    assert vm.is_pending is False


def test_handle_option_deny_dispatches_verdict_and_clears() -> None:
    vm, host = _vm()
    vm.begin(_event())

    vm.handle_option("deny")

    assert host.started_approvals == [("run-1", ApprovalVerdict(ApprovalDecision.DENY))]
    assert vm.is_pending is False


def test_handle_option_allow_tool_sets_policy_override_then_dispatches_approve() -> None:
    vm, host = _vm()
    vm.begin(_event(tool="bash"))

    vm.handle_option("allow_tool")

    assert host.session.policy.allowed == ["bash"]
    assert host.started_approvals == [("run-1", ApprovalVerdict(ApprovalDecision.APPROVE))]


def test_handle_option_allow_all_auto_approves_then_dispatches_approve() -> None:
    vm, host = _vm()
    vm.begin(_event())

    vm.handle_option("allow_all")

    assert host.session.policy.auto_approve_calls == 1
    assert host.started_approvals == [("run-1", ApprovalVerdict(ApprovalDecision.APPROVE))]


def test_handle_option_deny_reason_switches_into_reason_mode() -> None:
    vm, host = _vm()
    vm.begin(_event())

    vm.handle_option("deny_reason")

    assert vm.deny_reason_mode is True
    assert vm.has_menu is False
    assert host._prompt_input.disabled is False
    assert "denial" in host._prompt_input.placeholder.lower()


def test_handle_option_when_not_pending_is_noop() -> None:
    vm, host = _vm()

    vm.handle_option("approve")

    assert host.started_approvals == []


# ── handle_text_input shorthand ─────────────────────────────────────


def test_handle_text_input_returns_false_when_not_pending() -> None:
    vm, host = _vm()

    assert vm.handle_text_input("yes") is False
    assert host.started_approvals == []


def test_handle_text_input_unknown_shorthand_prints_hint_and_consumes_input() -> None:
    vm, host = _vm()
    vm.begin(_event())

    consumed = vm.handle_text_input("not a command")

    assert consumed is True
    assert host.started_approvals == []
    assert any(host.appended)


def test_handle_text_input_yes_dispatches_approve() -> None:
    vm, host = _vm()
    vm.begin(_event())

    consumed = vm.handle_text_input("y")

    assert consumed is True
    assert host.started_approvals == [("run-1", ApprovalVerdict(ApprovalDecision.APPROVE))]


def test_handle_text_input_no_with_reason_dispatches_deny_with_reason() -> None:
    vm, host = _vm()
    vm.begin(_event())

    consumed = vm.handle_text_input("n because too risky")

    assert consumed is True
    assert host.started_approvals == [
        ("run-1", ApprovalVerdict(ApprovalDecision.DENY, denial_reason="too risky"))
    ]


# ── deny reason mode ────────────────────────────────────────────────


def test_deny_reason_with_empty_input_keeps_mode_and_prints_hint() -> None:
    vm, host = _vm()
    vm.begin(_event())
    vm.handle_option("deny_reason")

    consumed = vm.handle_text_input("   ")

    assert consumed is True
    assert vm.deny_reason_mode is True
    assert host.started_approvals == []


def test_deny_reason_with_text_dispatches_deny_with_reason_and_clears() -> None:
    vm, host = _vm()
    vm.begin(_event())
    vm.handle_option("deny_reason")

    vm.handle_text_input("explains policies")

    assert host.started_approvals == [
        ("run-1", ApprovalVerdict(ApprovalDecision.DENY, denial_reason="explains policies"))
    ]
    assert vm.deny_reason_mode is False
    assert vm.is_pending is False


def test_deny_reason_input_strips_surrounding_whitespace() -> None:
    vm, host = _vm()
    vm.begin(_event())
    vm.handle_option("deny_reason")

    vm.handle_text_input("   safety first  ")

    assert host.started_approvals == [
        ("run-1", ApprovalVerdict(ApprovalDecision.DENY, denial_reason="safety first"))
    ]
