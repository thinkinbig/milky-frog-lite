import multiprocessing
from pathlib import Path

import pytest

from milky_frog.checkpoint import RunClaimError, SqliteCheckpointStore
from milky_frog.checkpoint.snapshot import dump_run_state, load_run_state
from milky_frog.domain import MessageRole, RunState, RunStatus
from milky_frog.harness.state import append_user_message, start_run
from tests.checkpoint_helpers import seed_run


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


def test_sqlite_store_persists_state_and_projects_status(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    state = seed_run(store, "run-1", workspace, prompt="hello")
    store.save_state(state.run_id, state, status=RunStatus.COMPLETED, final_message="done")

    loaded = store.load_state("run-1")
    assert [message.role for message in loaded.messages] == [
        MessageRole.SYSTEM,
        MessageRole.USER,
    ]
    assert loaded.messages[-1].content == "hello"
    run = store.get_run("run-1")
    assert run is not None
    assert run.status is RunStatus.COMPLETED
    assert run.final_message == "done"


def test_sqlite_store_prepares_resume_atomically(tmp_path: Path) -> None:
    store = SqliteCheckpointStore(tmp_path / "state.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    store.create_run("run-1", workspace)
    base = start_run(RunState(run_id="run-1", workspace=workspace), "hello")
    store.save_state("run-1", base, status=RunStatus.COMPLETED, final_message="done")
    stored = store.get_run("run-1")
    assert stored is not None
    resumed = append_user_message(store.load_state("run-1"), "follow up")
    store.prepare_resume("run-1", stored.updated_at, resumed)

    run = store.get_run("run-1")
    assert run is not None
    assert run.status is RunStatus.RUNNING
    loaded = store.load_state("run-1")
    assert user_messages(loaded) == ("hello", "follow up")


def user_messages(state: RunState) -> tuple[str, ...]:
    return tuple(message.content for message in state.messages if message.role is MessageRole.USER)


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
    state = start_run(RunState(run_id="run-1", workspace=tmp_path), "hello")
    store.save_state("run-1", state, status=RunStatus.COMPLETED, final_message="done")

    follow_up = append_user_message(state, "lost")
    with pytest.raises(RuntimeError, match="changed"):
        store.prepare_resume("run-1", created.updated_at, follow_up)

    run = store.get_run("run-1")
    assert run is not None
    assert run.status is RunStatus.COMPLETED
    loaded = store.load_state("run-1")
    assert user_messages(loaded) == ("hello",)


def test_snapshot_rejects_invalid_role() -> None:
    state = start_run(RunState(run_id="run-1", workspace=Path(".")), "hello")
    raw = dump_run_state(state).replace('"role":"user"', '"role":"not-a-role"')
    with pytest.raises(ValueError, match="not a valid MessageRole"):
        load_run_state("run-1", Path("."), raw)


def test_snapshot_round_trips_run_state(tmp_path: Path) -> None:
    state = start_run(RunState(run_id="run-1", workspace=tmp_path), "hello")
    loaded = load_run_state("run-1", tmp_path, dump_run_state(state))
    assert loaded == state
