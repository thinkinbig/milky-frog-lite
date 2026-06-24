"""Unified tool-output truncation: head/tail token cap before the transcript."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.domain import (
    MessageRole,
    ModelChunk,
    ModelRequest,
    ModelResponse,
    RunRequest,
    StreamDone,
    ToolCall,
)
from milky_frog.handlers import EventDispatcher
from milky_frog.harness.tokens import ApproxCharCounter, truncate_tool_output
from milky_frog.harness.tools import ToolRegistry
from tests.stubs import EchoTool, make_harness

_COUNTER = ApproxCharCounter()


# ── Unit: truncate_tool_output ───────────────────────────────────────────


def test_empty_text_is_unchanged() -> None:
    assert truncate_tool_output("", limit_tokens=10) == ""


def test_text_under_limit_is_unchanged() -> None:
    text = "a short result"
    assert truncate_tool_output(text, limit_tokens=1000) == text


def test_text_at_exact_limit_is_unchanged() -> None:
    text = "a" * 40  # ApproxCharCounter: 40 // 4 == 10 tokens
    assert _COUNTER.count_text(text) == 10
    assert truncate_tool_output(text, limit_tokens=10) == text


def test_zero_limit_is_a_noop() -> None:
    text = "x" * 1000
    assert truncate_tool_output(text, limit_tokens=0) == text


def test_large_output_keeps_head_and_tail_with_marker() -> None:
    text = "HEAD" + ("x" * 200_000) + "TAIL"

    out = truncate_tool_output(text, limit_tokens=1000)

    assert out.startswith("HEAD")  # head window preserved
    assert out.endswith("TAIL")  # tail window preserved
    assert "tokens omitted" in out  # model can perceive the truncation
    assert _COUNTER.count_text(out) < _COUNTER.count_text(text)


def test_truncation_does_not_spill_to_disk() -> None:
    out = truncate_tool_output("x" * 200_000, limit_tokens=100)

    assert "/tmp" not in out
    assert "saved to" not in out


# ── Integration: the agent loop applies the cap before the transcript ────


class _HugeEchoModel:
    """Call ``echo`` with an oversized payload, then finish."""

    def __init__(self, payload: str) -> None:
        self._payload = payload
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        self.calls += 1
        if self.calls == 1:
            yield StreamDone(
                ModelResponse(tool_calls=(ToolCall("c1", "echo", {"text": self._payload}),))
            )
            return
        yield StreamDone(ModelResponse(content="done"))


@pytest.mark.asyncio
async def test_loop_truncates_tool_output_before_transcript(tmp_path: Path) -> None:
    payload = "y" * 200_000
    store = SqliteCheckpointStore(tmp_path / "state.db")
    harness = make_harness(
        model=_HugeEchoModel(payload),
        tools=ToolRegistry((EchoTool(),)),
        checkpoints=store,
        handlers=EventDispatcher(),
    )

    result = await harness.run(RunRequest("go", tmp_path))

    loaded = store.load_state(result.run_id)
    tool_message = [m for m in loaded.messages if m.role is MessageRole.TOOL][-1]
    assert "tokens omitted" in tool_message.content
    assert len(tool_message.content) < len(payload)
