from pathlib import Path

import pytest
from pydantic import ValidationError

from milky_frog.checkpoint import RunEvent, SqliteCheckpointStore
from milky_frog.domain import RunStatus


def test_sqlite_store_appends_events_and_projects_status(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    store.create_run("run-1", workspace)
    first = store.append(
        "run-1",
        RunEvent.from_parts("RunStarted", {"prompt": "hello", "workspace": str(workspace)}),
    )
    second = store.append(
        "run-1",
        RunEvent.from_parts("RunCompleted", {"final_message": "done"}),
        RunStatus.COMPLETED,
    )

    assert first.sequence == 1
    assert second.sequence == 2
    assert [event.event_type for event in store.events("run-1")] == [
        "RunStarted",
        "RunCompleted",
    ]
    run = store.get_run("run-1")
    assert run is not None
    assert run.status is RunStatus.COMPLETED


def test_run_event_rejects_unknown_event_type() -> None:
    with pytest.raises(ValidationError):
        RunEvent.from_parts("NotARealEvent", {"prompt": "hello"})


def test_run_event_rejects_missing_required_fields() -> None:
    with pytest.raises(ValidationError):
        RunEvent.from_parts("RunStarted", {"prompt": "hello"})
