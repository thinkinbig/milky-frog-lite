from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.domain import ModelChunk, ModelRequest, ModelResponse, RunStatus, StreamDone
from milky_frog.models import OpenAIModel
from milky_frog.runtime import MilkyFrog, MissingModelConfiguration
from milky_frog.settings import Settings


def test_milky_frog_runs_through_configured_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    requests: list[ModelRequest] = []

    async def fake_stream(self: OpenAIModel, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del self
        requests.append(request)
        yield StreamDone(ModelResponse(content="done"))

    monkeypatch.setattr(OpenAIModel, "stream", fake_stream)
    settings = Settings(tmp_path, "test-key", "https://example.test", "test-model")

    result = MilkyFrog.from_settings(settings).run("build it", tmp_path)

    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "done"
    assert requests[0].messages[0].role.value == "system"
    assert requests[0].messages[1].content == "build it"
    assert SqliteCheckpointStore(settings.database_path).get_run(result.run_id) is not None


def test_milky_frog_rejects_missing_model_configuration(tmp_path: Path) -> None:
    settings = Settings(tmp_path, None, None, None)

    with pytest.raises(MissingModelConfiguration, match="model configuration is missing"):
        MilkyFrog.from_settings(settings)


@pytest.mark.parametrize("api_key,model", [("", "test-model"), ("test-key", ""), ("", "")])
def test_milky_frog_rejects_empty_model_configuration(
    tmp_path: Path, api_key: str, model: str
) -> None:
    settings = Settings(tmp_path, api_key, None, model)

    with pytest.raises(MissingModelConfiguration, match="model configuration is missing"):
        MilkyFrog.from_settings(settings)
