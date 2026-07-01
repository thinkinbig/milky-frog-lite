from collections.abc import AsyncIterator
from pathlib import Path

from milky_frog.checkpoint.snapshot import dump_run_state, load_run_state
from milky_frog.domain import (
    Compacted,
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
from milky_frog.events.loop import _apply_control
from milky_frog.harness.compaction import CompactionHandler
from milky_frog.harness.context import ContextManager


class _StubSummaryModel:
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        yield StreamDone(ModelResponse(content="SUMMARY"))


class _RecordingModel:
    """Captures the last request streamed, to inspect the summarizer prompt."""

    def __init__(self) -> None:
        self.seen: ModelRequest | None = None

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        self.seen = request
        yield StreamDone(ModelResponse(content="SUMMARY"))


class _CharCounter:
    """Token counter stub: one token per character of content, no framing."""

    def count_text(self, text: str) -> int:
        return len(text)

    def count_messages(self, messages: list[dict[str, str]]) -> int:
        return sum(len(m["content"]) for m in messages)

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


def test_apply_control_folds_compacted_into_state() -> None:
    state = _state((Message(MessageRole.USER, "hi"),))
    compaction = CompactionState(summary="earlier", through_index=1)

    updated = _apply_control(state, [Compacted(compaction)])

    assert updated.compaction == compaction
    assert state.compaction is None  # the original state is untouched


def test_apply_control_returns_same_state_when_no_results() -> None:
    state = _state((Message(MessageRole.USER, "hi"),))

    assert _apply_control(state, []) is state


def _handler(*, trigger_tokens: int, keep_recent_tokens: int) -> CompactionHandler:
    return CompactionHandler(
        _StubSummaryModel(),
        _CharCounter(),
        trigger_tokens=trigger_tokens,
        keep_recent_tokens=keep_recent_tokens,
    )


async def _broadcast(handler: CompactionHandler, state: RunState) -> list[Compacted]:
    hub = EventHub()
    handler.register(hub)
    # The handler measures the assembled request; a request over the state's raw
    # transcript is a faithful stand-in (the handler does not care how it is built).
    request = ModelRequest(state.messages, ())
    results = await hub.broadcast(RunBeforeModel(run_id="run-1", request=request, state=state))
    return [r for r in results if isinstance(r, Compacted)]


async def test_handler_compacts_when_over_budget() -> None:
    # 10 messages, each content length 2 → request ≈ 20 tokens > trigger.
    state = _state(_rounds(5))

    results = await _broadcast(_handler(trigger_tokens=5, keep_recent_tokens=5), state)

    assert len(results) == 1
    compacted = results[0]
    assert isinstance(compacted, Compacted)
    # keep_recent_tokens=5 keeps the last ~5 tokens (indices 7-9); summarize through 7.
    assert compacted.compaction.through_index == 7
    assert compacted.compaction.summary == "SUMMARY"


async def test_handler_skips_when_under_budget() -> None:
    state = _state(_rounds(5))  # ≈ 20 tokens, below trigger

    results = await _broadcast(_handler(trigger_tokens=1000, keep_recent_tokens=5), state)

    assert results == []


async def test_handler_skips_when_whole_tail_fits() -> None:
    # Over the trigger, but the whole transcript fits the keep window → nothing to cut.
    state = _state(_rounds(5))

    results = await _broadcast(_handler(trigger_tokens=5, keep_recent_tokens=1000), state)

    assert results == []


async def test_handler_does_not_resummarize_same_range() -> None:
    # Already summarized through index 7; the cutoff would also be 7 → no new work.
    state = _state(_rounds(5), compaction=CompactionState(summary="old", through_index=7))

    results = await _broadcast(_handler(trigger_tokens=5, keep_recent_tokens=5), state)

    assert results == []


async def test_summarize_is_incremental_and_excludes_prior_prefix() -> None:
    # Prior summary covers messages[:4]; a new compaction must feed the summarizer
    # only the newly dropped slice messages[4:cutoff], not the whole prefix again.
    model = _RecordingModel()
    handler = CompactionHandler(model, _CharCounter(), trigger_tokens=5, keep_recent_tokens=5)
    state = _state(_rounds(5), compaction=CompactionState(summary="PRIOR", through_index=4))
    hub = EventHub()
    handler.register(hub)

    await hub.broadcast(
        RunBeforeModel(run_id="run-1", request=ModelRequest(state.messages, ()), state=state)
    )

    assert model.seen is not None
    prompt = model.seen.messages[-1].content
    assert "PRIOR" in prompt  # prior summary is carried forward
    assert "u2" in prompt  # a newly dropped message (index 4)
    assert "u0" not in prompt  # already covered by PRIOR — never re-fed
