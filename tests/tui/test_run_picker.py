from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input

from milky_frog.app.session import AgentSession
from milky_frog.checkpoint import SqliteCheckpointStore, StoredRun
from milky_frog.core.controller import RunController
from milky_frog.core.runtime.checkpoint import RunCheckpointFacade
from milky_frog.domain import RunStatus
from milky_frog.settings import Settings
from milky_frog.tui.app import MilkyFrogApp
from milky_frog.tui.messages import RunOptionSelected
from milky_frog.tui.widgets.run_picker import RunPicker
from tests.checkpoint_helpers import seed_run


def _stored_run(
    run_id: str = "run-abc123",
    *,
    final_message: str | None = "Finished the requested change",
) -> StoredRun:
    now = datetime.now(UTC)
    return StoredRun(
        run_id=run_id,
        workspace=Path("/workspace"),
        status=RunStatus.COMPLETED,
        created_at=now,
        updated_at=now,
        final_message=final_message,
    )


def test_workspace_runs_excludes_other_workspaces(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    workspace = tmp_path / "workspace"
    other_workspace = tmp_path / "other"
    workspace.mkdir()
    other_workspace.mkdir()
    seed_run(store, "run-current", workspace, status=RunStatus.COMPLETED)
    seed_run(store, "run-other", other_workspace, status=RunStatus.COMPLETED)
    controller = RunController(RunCheckpointFacade(store))

    assert [run.run_id for run in controller.workspace_runs(workspace)] == ["run-current"]


def test_run_picker_uses_short_id_status_and_summary() -> None:
    picker = RunPicker((_stored_run(),))

    label = picker._option_label(_stored_run()).plain

    assert "run-abc1" in label
    assert "completed" in label
    assert "Finished the requested change" in label


class _PickerApp(App[None]):
    def __init__(self, runs: tuple[StoredRun, ...]) -> None:
        super().__init__()
        self.selected_run_id: str | None | object = object()
        self._runs = runs

    def compose(self) -> ComposeResult:
        yield RunPicker(self._runs)

    def on_run_option_selected(self, event: RunOptionSelected) -> None:
        self.selected_run_id = event.run_id


@pytest.mark.asyncio
async def test_run_picker_selects_highlighted_run_with_arrow_and_enter() -> None:
    app = _PickerApp((_stored_run("run-first"), _stored_run("run-second")))

    async with app.run_test() as pilot:
        await pilot.press("down")
        await pilot.press("enter")

    assert app.selected_run_id == "run-second"


@pytest.mark.asyncio
async def test_run_picker_escape_posts_empty_selection() -> None:
    app = _PickerApp((_stored_run(),))

    async with app.run_test() as pilot:
        await pilot.press("escape")

    assert app.selected_run_id is None


@pytest.mark.asyncio
async def test_bare_resume_selects_a_run_and_starts_existing_attach_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = AgentSession(
        Settings(home=tmp_path, api_key="test-key", model="test-model", _env_file=None),
        bundles=[],
        interactive=True,
    )
    controller = MagicMock(spec=RunController)
    controller.workspace_runs.return_value = (_stored_run("run-first"), _stored_run("run-second"))
    app = MilkyFrogApp(session, controller)
    attached: list[tuple[str, bool]] = []

    def attach(run_id: str, *, advance_pending: bool = False, **_kwargs: object) -> None:
        attached.append((run_id, advance_pending))

    monkeypatch.setattr(app, "_attach_or_continue_run", attach)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt-input", Input)
        prompt.value = "/resume"
        await pilot.press("enter")
        assert app.query_one(RunPicker).has_focus_within
        await pilot.press("down")
        await pilot.press("enter")

    assert attached == [("run-second", True)]


@pytest.mark.asyncio
async def test_bare_resume_without_workspace_runs_renders_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = AgentSession(
        Settings(home=tmp_path, api_key="test-key", model="test-model", _env_file=None),
        bundles=[],
        interactive=True,
    )
    controller = MagicMock(spec=RunController)
    controller.workspace_runs.return_value = ()
    app = MilkyFrogApp(session, controller)
    errors: list[tuple[str, str | None]] = []

    def render_error(message: str, *, hint: str | None = None) -> None:
        errors.append((message, hint))

    monkeypatch.setattr(app._conv, "render_error", render_error)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt-input", Input)
        prompt.value = "/resume"
        await pilot.press("enter")

    assert errors == [("No runs found to resume.", "Start a new task to create a run first.")]
