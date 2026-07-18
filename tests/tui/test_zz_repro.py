from __future__ import annotations

from milky_frog.tui.messages import ApprovalRequired, PendingApproval
from milky_frog.tui.viewmodels import approval_vm
from milky_frog.tui.viewmodels.approval_vm import ApprovalViewModel
from tests.tui._fakes import FakeApprovalPrompt, FakeTuiHost


def _vm(monkeypatch):
    monkeypatch.setattr(approval_vm, "ApprovalPrompt", FakeApprovalPrompt)
    host = FakeTuiHost()
    return ApprovalViewModel(host), host


def test_interleaved_allow_tool_reprompts(monkeypatch):
    vm, host = _vm(monkeypatch)
    # bash, fetch, bash  -> allow_tool on first bash should cover call-3
    vm.begin(ApprovalRequired(run_id="run-1", approvals=(
        PendingApproval("call-1", "bash", "1?"),
        PendingApproval("call-2", "fetch", "2?"),
        PendingApproval("call-3", "bash", "3?"),
    )))
    vm.handle_option("allow_tool")
    p = host._conversation_widget.mounted_widgets[-1]
    print("after allow_tool -> position", p.position, "tool", p.tool_name)
    vm.handle_option("approve")   # decide the fetch
    print("dispatched?", host.started_approvals)
    if not host.started_approvals:
        p = host._conversation_widget.mounted_widgets[-1]
        print("RE-PROMPTED position", p.position, "tool", p.tool_name, "<-- already had a verdict")
        print("verdicts so far", vm._verdicts)


def test_empty_batch_dispatches_immediately(monkeypatch):
    vm, host = _vm(monkeypatch)
    host.session.busy = True
    vm.begin(ApprovalRequired(run_id="run-1", approvals=()))
    print("started_approvals:", host.started_approvals)
    print("session.busy after begin():", host.session.busy)
    print("pending_approval:", host.session.pending_approval)
