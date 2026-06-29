from collections.abc import AsyncIterator
from pathlib import Path

from milky_frog.checkpoint.snapshot import dump_run_state, load_run_state
from milky_frog.core.handlers import Compacted
from milky_frog.domain import (
    CompactionState,
    Message,
    MessageRole,
    ModelChunk,
    ModelRequest,
    ModelResponse,
    RunState,
    StreamDone,
)
from milky_frog.events.events import RunBeforeModel
from milky_frog.events.hub import EventHub
from milky_frog.harness.compaction import CompactionHandler
from milky_frog.harness.context import ContextManager


class _StubSummaryModel:
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        yield StreamDone(ModelResponse(content="SUMMARY"))


class _Counter:
    """Token counter stub returning a fixed total, to drive the trigger."""

    def __init__(self, total: int) -> None:
        self._total = total

    def count_text(self, text: str) -> int:
        return self._total

    def count_messages(self, messages: list[dict[str, str]]) -> int:
        return self._total

    def count_tool_schemas(self, tools: object) -> int:
        return 0


def _rounds(n: int) -> tuple[Message, ...]:
    messages: list[Message] = []
    for i in range(n):
        messages.append(Message(MessageRole.USER, f"u{i}"))
        messages.append(Message(MessageRole.ASSISTANT, f"a{i}"))
    return tuple(messages)


def _state(messages: tuple[Message, ...], compaction: CompactionState | None = None) -> RunState:
    return RunState(
        run_id="run-1", workspace=Path("/tmp"), messages=messages, compaction=compaction
    )


def test_snapshot_round_trips_compaction(tmp_path: Path) -> None:
    state = _state(
        (Message(MessageRole.USER, "hi"), Message(MessageRole.ASSISTANT, "yo")),
        compaction=CompactionState(summary="they greeted", through_index=1),
    )

    loaded = load_run_state("run-1", tmp_path, dump_run_state(state))

    assert loaded.compaction == CompactionState(summary="they greeted", through_index=1)
    # Originals are preserved in full — compaction never deletes them.
    assert loaded.messages == state.messages


def test_snapshot_without_compaction_is_none(tmp_path: Path) -> None:
    state = _state((Message(MessageRole.USER, "hi"),))

    loaded = load_run_state("run-1", tmp_path, dump_run_state(state))

    assert loaded.compaction is None


def test_assemble_replaces_prefix_with_summary() -> None:
    messages = (
        Message(MessageRole.USER, "old 1"),
        Message(MessageRole.ASSISTANT, "old 2"),
        Message(MessageRole.USER, "recent"),
    )
    state = _state(messages, compaction=CompactionState(summary="earlier stuff", through_index=2))

    assembled = ContextManager().assemble(state)

    assert assembled[0].role is MessageRole.SYSTEM
    assert assembled[1].role is MessageRole.USER
    assert "earlier stuff" in assembled[1].content
    # Only the tail after through_index survives verbatim.
    assert assembled[2] == Message(MessageRole.USER, "recent")
    assert len(assembled) == 3


def test_assemble_without_compaction_keeps_full_history() -> None:
    messages = (Message(MessageRole.USER, "a"), Message(MessageRole.ASSISTANT, "b"))
    state = _state(messages)

    assembled = ContextManager().assemble(state)

    assert assembled[0].role is MessageRole.SYSTEM
    assert assembled[1:] == messages


def _handler(total_tokens: int, keep: int = 2) -> CompactionHandler:
    return CompactionHandler(
        _StubSummaryModel(), _Counter(total_tokens), trigger_tokens=1000, keep_recent_rounds=keep
    )


async def test_handler_compacts_when_over_budget() -> None:
    state = _state(_rounds(5))  # 10 messages, user-starts at 0,2,4,6,8
    hub = EventHub()
    _handler(total_tokens=2000, keep=2).register(hub)

    results = await hub.broadcast(
        RunBeforeModel(run_id="run-1", request=ModelRequest((), ()), state=state)
    )

    assert len(results) == 1
    compacted = results[0]
    assert isinstance(compacted, Compacted)
    # keep_recent_rounds=2 keeps the last 2 rounds (indices 6-9); summarize through 6.
    assert compacted.compaction.through_index == 6
    assert compacted.compaction.summary == "SUMMARY"


async def test_handler_skips_when_under_budget() -> None:
    state = _state(_rounds(5))
    hub = EventHub()
    _handler(total_tokens=10).register(hub)  # below trigger_tokens=1000

    results = await hub.broadcast(
        RunBeforeModel(run_id="run-1", request=ModelRequest((), ()), state=state)
    )

    assert results == []


async def test_handler_skips_when_too_few_rounds() -> None:
    state = _state(_rounds(2))  # only 2 rounds, keep_recent_rounds=2 → nothing old enough
    hub = EventHub()
    _handler(total_tokens=2000, keep=2).register(hub)

    results = await hub.broadcast(
        RunBeforeModel(run_id="run-1", request=ModelRequest((), ()), state=state)
    )

    assert results == []


async def test_handler_does_not_resummarize_same_range() -> None:
    # Already summarized through index 6; the cutoff would also be 6 → no new work.
    state = _state(_rounds(5), compaction=CompactionState(summary="old", through_index=6))
    hub = EventHub()
    _handler(total_tokens=2000, keep=2).register(hub)

    results = await hub.broadcast(
        RunBeforeModel(run_id="run-1", request=ModelRequest((), ()), state=state)
    )

    assert results == []
