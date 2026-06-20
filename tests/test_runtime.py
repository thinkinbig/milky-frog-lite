from __future__ import annotations

from pathlib import Path

import pytest

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.domain import ModelRequest, ModelResponse, RunStatus
from milky_frog.models import OpenAIModel
from milky_frog.runtime import MilkyFrog, MissingModelConfiguration
from milky_frog.settings import LangfuseSettings, Settings

_NO_LANGFUSE = LangfuseSettings(
    enabled=False, public_key=None, secret_key=None, host="https://cloud.langfuse.com"
)


def test_milky_frog_runs_through_configured_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    requests: list[ModelRequest] = []

    async def fake_complete(self: OpenAIModel, request: ModelRequest) -> ModelResponse:
        del self
        requests.append(request)
        return ModelResponse(content="done")

    monkeypatch.setattr(OpenAIModel, "complete", fake_complete)
    settings = Settings(tmp_path, "test-key", "https://example.test", "test-model", _NO_LANGFUSE)

    result = MilkyFrog.from_settings(settings).run("build it", tmp_path)

    assert result.status is RunStatus.COMPLETED
    assert result.final_message == "done"
    assert requests[0].messages[0].role.value == "system"
    assert requests[0].messages[1].content == "build it"
    assert SqliteCheckpointStore(settings.database_path).get_run(result.run_id) is not None


def test_milky_frog_rejects_missing_model_configuration(tmp_path: Path) -> None:
    settings = Settings(tmp_path, None, None, None, _NO_LANGFUSE)

    with pytest.raises(MissingModelConfiguration, match="model configuration is missing"):
        MilkyFrog.from_settings(settings)


@pytest.mark.parametrize("api_key,model", [("", "test-model"), ("test-key", ""), ("", "")])
def test_milky_frog_rejects_empty_model_configuration(
    tmp_path: Path, api_key: str, model: str
) -> None:
    settings = Settings(tmp_path, api_key, None, model, _NO_LANGFUSE)

    with pytest.raises(MissingModelConfiguration, match="model configuration is missing"):
        MilkyFrog.from_settings(settings)
