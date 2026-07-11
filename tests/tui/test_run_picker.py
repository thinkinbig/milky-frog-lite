from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from milky_frog.checkpoint import StoredRun
from milky_frog.domain import RunStatus
from milky_frog.tui.messages import RunOptionSelected
from milky_frog.tui.widgets.run_picker import RunPicker


def _run(run_id: str = "abcdefghijk") -> StoredRun:
    moment = datetime(2026, 7, 11, 12, 30, tzinfo=UTC)
    return StoredRun(
        run_id,
        workspace=Path("/workspace"),
        status=RunStatus.COMPLETED,
        created_at=moment,
        updated_at=moment,
        final_message="Finished the task",
    )


def test_picker_displays_short_id_status_time_and_summary() -> None:
    picker = RunPicker((_run(),))

    children = tuple(picker.compose())
    options = children[1]
    option = options.get_option_at_index(0)

    assert option.id == "abcdefghijk"
    assert "abcdefgh" in str(option.prompt)
    assert "completed" in str(option.prompt)
    assert "Finished the task" in str(option.prompt)


def test_picker_posts_selected_run_id() -> None:
    picker = RunPicker((_run("run-one"),))
    posted: list[RunOptionSelected] = []
    picker.post_message = posted.append  # type: ignore[method-assign]

    picker.on_option_list_option_selected(SimpleNamespace(option=SimpleNamespace(id="run-one")))

    assert posted[0].run_id == "run-one"


def test_picker_dismiss_posts_no_selection() -> None:
    picker = RunPicker((_run(),))
    posted: list[RunOptionSelected] = []
    picker.post_message = posted.append  # type: ignore[method-assign]

    picker.action_dismiss()

    assert posted[0].run_id is None
