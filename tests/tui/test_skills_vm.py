"""Tests for ``SkillsViewModel.handle_command`` — the ``/skill [...]`` dispatcher.

The VM talks to its host (the App) through the ``TuiHost`` Protocol. We stub
the host via ``tests/tui/_fakes.py`` and use the bundled skills shipped in
``src/milky_frog/harness/skills/bundled/`` as a real catalog fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from milky_frog.tui.viewmodels import skills_vm
from milky_frog.tui.viewmodels.skills_vm import SkillsViewModel
from tests.tui._fakes import FakeSkillPicker, FakeTuiHost


@pytest.fixture(autouse=True)
def _stub_skill_picker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(skills_vm, "SkillPicker", FakeSkillPicker)


def _vm() -> tuple[SkillsViewModel, FakeTuiHost]:
    host = FakeTuiHost()
    return SkillsViewModel(host), host  # type: ignore[arg-type]


def test_initial_state_has_no_skills_active() -> None:
    vm, _ = _vm()
    assert vm.active == frozenset()
    assert vm.touched is False
    assert vm.has_picker is False


def test_handle_command_off_deactivates_all_and_updates_placeholder() -> None:
    vm, host = _vm()
    # Pre-condition: input placeholder is the default.
    assert host._prompt_input.placeholder == ""

    vm.handle_command("/skill off")

    assert vm.active == frozenset()
    assert vm.touched is True
    # Placeholder was reset to default.
    assert host._prompt_input.placeholder == "Type a task and press Enter..."


def test_handle_command_unknown_name_appends_error() -> None:
    vm, host = _vm()
    initial_appended = len(host.appended)

    vm.handle_command("/skill nope-not-a-skill")

    assert vm.active == frozenset()
    assert vm.touched is False
    # An error row was appended (the catalog lookup failed).
    assert len(host.appended) > initial_appended


def test_handle_command_toggles_active_skill_set() -> None:
    vm, _ = _vm()
    first = next(summary.name for summary in _bundled_summaries())

    vm.handle_command(f"/skill {first}")  # add
    assert first in vm.active
    assert vm.touched is True

    vm.handle_command(f"/skill {first}")  # remove
    assert first not in vm.active
    assert vm.touched is True


def test_handle_command_with_no_args_when_picker_already_shown_replaces_prior_picker() -> None:
    """Calling ``/skill`` with no arguments shows the picker; calling again
    while it's mounted removes the prior picker and mounts a fresh one,
    rather than stacking both."""
    vm, host = _vm()

    vm.handle_command("/skill")  # first mount
    first = host._conversation_widget.mounted_widgets[-1]
    assert first.removed is False

    vm.handle_command("/skill")  # second mount
    second = host._conversation_widget.mounted_widgets[-1]

    # The previous picker was unmounted (``removed=True``); the new one is not.
    assert first.removed is True
    assert second.removed is False
    assert first is not second


def test_dismiss_picker_fires_dismiss_action_on_open_picker() -> None:
    """``dismiss_picker`` fires the picker's dismiss action; the App is
    responsible for actually unmounting the widget when it processes the
    resulting ``SkillOptionSelected`` message."""
    vm, host = _vm()
    vm.handle_command("/skill")
    picker = host._conversation_widget.mounted_widgets[-1]
    assert picker.removed is False

    vm.dismiss_picker()

    assert picker.removed is True


def test_dismiss_picker_when_none_mounted_is_noop() -> None:
    vm, _ = _vm()
    vm.dismiss_picker()  # never opened anything
    assert vm.has_picker is False


# ── Helpers ─────────────────────────────────────────────────────────


_BUNDLED_ROOT = (
    Path(__file__).resolve().parents[2] / "src" / "milky_frog" / "harness" / "skills" / "bundled"
)


def _bundled_summaries():
    from milky_frog.harness.skills.catalog import SkillCatalog

    return SkillCatalog(_BUNDLED_ROOT, Path("/nonexistent")).summaries()
