from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from milky_frog.app.session import AgentSession
from milky_frog.checkpoint import StoredRun
from milky_frog.core.controller import RunController
from milky_frog.domain import RunStatus
from milky_frog.settings import Settings
from milky_frog.tui.app import MilkyFrogApp
from milky_frog.tui.messages import RunOptionSelected


def _app(tmp_path: Path) -> tuple[MilkyFrogApp, MagicMock]:
    session = AgentSession(
        Settings(home=tmp_path, api_key="test-key", model="test-model", _env_file=None),
        bundles=[],
        interactive=True,
    )
    controller = MagicMock()
    return MilkyFrogApp(session, cast(RunController, controller)), controller


def test_bare_resume_opens_picker_for_current_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, controller = _app(tmp_path)
    opened = False

    def show_picker() -> None:
        nonlocal opened
        opened = True

    monkeypatch.setattr(app, "_show_run_picker", show_picker)

    app._handle_resume("/resume")

    assert opened is True
    controller.parse_resume_command.assert_not_called()


def test_selected_run_uses_existing_attach_path_with_pending_advance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = _app(tmp_path)
    attached: list[tuple[str, bool]] = []
    monkeypatch.setattr(app, "_dismiss_run_picker", lambda: None)

    def attach(run_id: str, *, advance_pending: bool = False) -> None:
        attached.append((run_id, advance_pending))

    monkeypatch.setattr(app, "_attach_or_continue_run", attach)

    app.on_run_option_selected(RunOptionSelected("run-one"))

    assert attached == [("run-one", True)]


def test_cancelled_picker_does_not_attach_a_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = _app(tmp_path)
    monkeypatch.setattr(app, "_dismiss_run_picker", lambda: None)
    attached = False

    def attach(_run_id: str, *, advance_pending: bool = False) -> None:
        nonlocal attached
        del advance_pending
        attached = True

    monkeypatch.setattr(app, "_attach_or_continue_run", attach)

    app.on_run_option_selected(RunOptionSelected(None))

    assert attached is False


@pytest.mark.asyncio
async def test_picker_enter_attaches_selected_run_and_escape_cancels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, controller = _app(tmp_path)
    moment = datetime.now(UTC)
    controller.workspace_runs.return_value = (
        StoredRun(
            "selected-run",
            tmp_path,
            RunStatus.COMPLETED,
            moment,
            moment,
            "done",
        ),
    )
    attached: list[str] = []

    def attach(run_id: str, *, advance_pending: bool = False) -> None:
        assert advance_pending is True
        attached.append(run_id)

    monkeypatch.setattr(app, "_attach_or_continue_run", attach)

    async with app.run_test() as pilot:
        app._handle_resume("/resume")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert attached == ["selected-run"]

        app._handle_resume("/resume")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert attached == ["selected-run"]
