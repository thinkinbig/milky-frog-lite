from __future__ import annotations

from pathlib import Path

import pytest

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.domain import RunRequest, RunStatus
from milky_frog.handlers import EventHub, RunFailed, RunNotice
from milky_frog.harness.model_retry import is_retriable_model_error
from milky_frog.harness.tools import ToolRegistry
from tests.stubs import FlakyConnectionModel, ImmediateErrorModel, make_harness


@pytest.fixture(autouse=True)
def instant_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Avoid real backoff delays; record retry wait durations only."""
    delays: list[float] = []

    async def instant_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("milky_frog.harness.agent_loop.retry_sleep", instant_sleep)
    return delays


def test_is_retriable_model_error_for_connection_failures() -> None:
    assert is_retriable_model_error(ConnectionError("offline")) is True
    assert is_retriable_model_error(TimeoutError()) is True
    assert is_retriable_model_error(ValueError("bad request")) is False


@pytest.mark.asyncio
async def test_retries_retriable_model_errors_and_emits_run_notice(
    tmp_path: Path, instant_retry_sleep: list[float]
) -> None:
    notices: list[RunNotice] = []
    bus = EventHub()

    @bus.on(RunNotice)
    async def record_notice(event: RunNotice, _ctx=None) -> None:
        notices.append(event)

    model = FlakyConnectionModel(failures=2)
    harness = make_harness(
        model=model,
        tools=ToolRegistry(),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        hub=bus,
    )

    result = await harness.run(RunRequest("hi", tmp_path))

    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "ok"
    assert model.calls == 3
    assert len(notices) == 2
    assert notices[0].level == "warning"
    assert "retrying (2/3)" in notices[0].message
    assert "retrying (3/3)" in notices[1].message
    assert instant_retry_sleep == [1.0, 2.0]


@pytest.mark.asyncio
async def test_exhausted_retries_emit_run_failed(tmp_path: Path) -> None:
    failed: list[RunFailed] = []
    bus = EventHub()

    @bus.on(RunFailed)
    async def record_failed(event: RunFailed, _ctx=None) -> None:
        failed.append(event)

    model = FlakyConnectionModel(failures=5)
    harness = make_harness(
        model=model,
        tools=ToolRegistry(),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        hub=bus,
    )

    result = await harness.run(RunRequest("hi", tmp_path))

    assert result.status is RunStatus.FAILED
    assert model.calls == 3
    assert len(failed) == 1
    assert "ConnectionError" in failed[0].result.final_message


@pytest.mark.asyncio
async def test_non_retriable_model_errors_do_not_retry(tmp_path: Path) -> None:
    notices: list[RunNotice] = []
    bus = EventHub()

    @bus.on(RunNotice)
    async def record_notice(event: RunNotice, _ctx=None) -> None:
        notices.append(event)

    model = ImmediateErrorModel(ValueError("bad request"))
    harness = make_harness(
        model=model,
        tools=ToolRegistry(),
        checkpoints=SqliteCheckpointStore(tmp_path / "state.db"),
        hub=bus,
    )

    result = await harness.run(RunRequest("hi", tmp_path))

    assert result.status is RunStatus.FAILED
    assert model.calls == 1
    assert notices == []


# ── retry_sleep ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_sleep_awaits_without_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from milky_frog.harness.model_retry import retry_sleep

    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    await retry_sleep(1.5)
    assert slept == [1.5]
