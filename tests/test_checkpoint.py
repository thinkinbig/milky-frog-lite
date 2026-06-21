import multiprocessing
from pathlib import Path

import pytest
from pydantic import ValidationError

from milky_frog.checkpoint import RunClaimError, RunEvent, SqliteCheckpointStore
from milky_frog.domain import RunStatus


def _hold_run_claim(database: str, acquired: object, release: object) -> None:
    acquired_event = acquired
    release_event = release
    store = SqliteCheckpointStore(Path(database))
    with store.claim("run-1"):
        acquired_event.set()
        release_event.wait()


def test_sqlite_store_resolve_run_id_by_unique_prefix(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store.create_run("abcdef0123456789", workspace)

    assert store.resolve_run_id("abcdef0123456789") == "abcdef0123456789"
    assert store.resolve_run_id("abcdef") == "abcdef0123456789"


def test_sqlite_store_resolve_run_id_rejects_unknown_and_ambiguous(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store.create_run("aaa111", workspace)
    store.create_run("aaa222", workspace)

    with pytest.raises(LookupError):
        store.resolve_run_id("missing")
    with pytest.raises(ValueError):
        store.resolve_run_id("aaa")


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


def test_sqlite_store_prepares_resume_atomically(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    created = store.create_run("run-1", workspace)
    event = RunEvent.from_parts("UserMessageAdded", {"content": "follow up"})
    persisted = store.prepare_resume("run-1", created.updated_at, (event,))

    run = store.get_run("run-1")
    assert run is not None
    assert run.status is RunStatus.RUNNING
    assert persisted[0].sequence == 1
    assert [item.event_type for item in store.events("run-1")] == ["UserMessageAdded"]


def test_sqlite_store_rejects_second_live_claim(tmp_path: Path) -> None:
    first = SqliteCheckpointStore(tmp_path / "state.db")
    second = SqliteCheckpointStore(tmp_path / "state.db")

    with (
        first.claim("run-1"),
        pytest.raises(RunClaimError, match="already active"),
        second.claim("run-1"),
    ):
        pytest.fail("second claim unexpectedly acquired")


def test_sqlite_store_claim_is_cross_process_and_released_on_exit(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_run_claim,
        args=(str(tmp_path / "state.db"), acquired, release),
    )
    process.start()
    try:
        assert acquired.wait(timeout=10)
        store = SqliteCheckpointStore(tmp_path / "state.db")
        with pytest.raises(RunClaimError, match="already active"), store.claim("run-1"):
            pytest.fail("claim unexpectedly acquired while child was alive")
        release.set()
        process.join(timeout=10)
        assert process.exitcode == 0
        with store.claim("run-1"):
            pass
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=10)


def test_sqlite_store_uses_canonical_database_path_for_claims(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    first = SqliteCheckpointStore(real / "state.db")
    second = SqliteCheckpointStore(alias / "state.db")

    with first.claim("run-1"), pytest.raises(RunClaimError), second.claim("run-1"):
        pytest.fail("symlink alias unexpectedly used a distinct claim")


def test_prepare_resume_rolls_back_on_stale_projection(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    created = store.create_run("run-1", tmp_path)
    store.append(
        "run-1",
        RunEvent.from_parts("RunCompleted", {"final_message": "done"}),
        RunStatus.COMPLETED,
    )

    follow_up = RunEvent.from_parts("UserMessageAdded", {"content": "lost"})
    with pytest.raises(RuntimeError, match="changed"):
        store.prepare_resume("run-1", created.updated_at, (follow_up,))

    run = store.get_run("run-1")
    assert run is not None
    assert run.status is RunStatus.COMPLETED
    assert [event.event_type for event in store.events("run-1")] == ["RunCompleted"]


def test_run_event_rejects_unknown_event_type() -> None:
    with pytest.raises(ValidationError):
        RunEvent.from_parts("NotARealEvent", {"prompt": "hello"})


def test_run_event_rejects_missing_required_fields() -> None:
    with pytest.raises(ValidationError):
        RunEvent.from_parts("RunStarted", {"prompt": "hello"})
