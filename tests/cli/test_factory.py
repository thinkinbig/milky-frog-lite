from __future__ import annotations

from pathlib import Path

import pytest

from milky_frog.cli.factory import HandlerFactory
from milky_frog.domain import ModelRequest, TextDelta
from milky_frog.handlers.events import RunModelChunk
from milky_frog.infra.observability.langfuse import LangfuseHandler
from milky_frog.settings import LangfuseSettings, Settings
from milky_frog.ui.handlers import StreamingHandlers
from milky_frog.ui.streaming import StreamingPrinter
from tests.stubs import LangfuseClientFactory

_ACTIVE = LangfuseSettings(
    enabled=True, public_key="public", secret_key="secret", host="https://langfuse.test"
)
_INACTIVE = LangfuseSettings(
    enabled=False, public_key=None, secret_key=None, host="https://langfuse.test"
)


def _settings(tmp_path: Path, langfuse: LangfuseSettings) -> Settings:
    return Settings(tmp_path, "key", None, "model", langfuse)


def test_factory_builds_ui_only_when_infrastructure_inactive(tmp_path: Path) -> None:
    _, bundles = HandlerFactory(_settings(tmp_path, _INACTIVE), StreamingPrinter()).build()

    assert [type(bundle) for bundle in bundles] == [StreamingHandlers]


def test_factory_composes_ui_and_active_infrastructure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "milky_frog.infra.observability.langfuse.Langfuse",
        LangfuseClientFactory(object()),
    )

    _, bundles = HandlerFactory(_settings(tmp_path, _ACTIVE), StreamingPrinter()).build()

    assert [type(bundle) for bundle in bundles] == [StreamingHandlers, LangfuseHandler]


@pytest.mark.asyncio
async def test_factory_registers_bundles_onto_returned_registry(tmp_path: Path) -> None:
    seen: list[str] = []

    class SpyPrinter(StreamingPrinter):
        def on_delta(self, text: str) -> None:
            seen.append(text)

    registry, _ = HandlerFactory(_settings(tmp_path, _INACTIVE), SpyPrinter()).build()
    await registry.notify(
        RunModelChunk(run_id="run-1", request=ModelRequest((), ()), chunk=TextDelta("hi"))
    )

    assert seen == ["hi"]
