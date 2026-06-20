from __future__ import annotations

import asyncio
from pathlib import Path

from milky_frog.checkpoint import SqliteCheckpointStore
from milky_frog.domain import RunRequest, RunResult
from milky_frog.handlers import HandlerRegistry
from milky_frog.harness import Harness
from milky_frog.models import OpenAIModel
from milky_frog.settings import Settings
from milky_frog.tools import ToolRegistry


class MissingModelConfiguration(ValueError):
    pass


class MilkyFrog:
    """Runs configured Milky Frog goals while hiding runtime assembly."""

    def __init__(self, settings: Settings) -> None:
        api_key = settings.api_key
        model = settings.model
        if api_key is None or model is None:
            raise MissingModelConfiguration("model configuration is missing")
        self._api_key = api_key
        self._model = model
        self._base_url = settings.base_url
        self._database_path = settings.database_path

    @classmethod
    def from_settings(cls, settings: Settings) -> MilkyFrog:
        return cls(settings)

    def run(self, prompt: str, workspace: Path) -> RunResult:
        """Run one goal synchronously.

        This uses `asyncio.run()`, so it must not be called when an event loop is already running.
        """
        harness = Harness(
            model=OpenAIModel(
                api_key=self._api_key,
                model=self._model,
                base_url=self._base_url,
            ),
            tools=ToolRegistry(),
            checkpoints=SqliteCheckpointStore(self._database_path),
            handlers=HandlerRegistry(),
        )
        return asyncio.run(harness.run(RunRequest(prompt, workspace)))
