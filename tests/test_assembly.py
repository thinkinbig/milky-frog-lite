from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from milky_frog.handlers import InfrastructureHandlerAssembly
from milky_frog.handlers.langfuse import LangfuseHandler
from milky_frog.settings import LangfuseSettings, Settings

_ACTIVE = LangfuseSettings(
    enabled=True, public_key="public", secret_key="secret", host="https://langfuse.test"
)
_INACTIVE = LangfuseSettings(
    enabled=False, public_key=None, secret_key=None, host="https://langfuse.test"
)


def _settings(tmp_path: Path, langfuse: LangfuseSettings) -> Settings:
    return Settings(tmp_path, "key", None, "model", langfuse)


def test_build_skips_inactive_infrastructure(tmp_path: Path) -> None:
    assert InfrastructureHandlerAssembly(_settings(tmp_path, _INACTIVE)).build() == []


def test_build_includes_active_langfuse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("milky_frog.handlers.langfuse.Langfuse", lambda **kwargs: object())

    bundles = InfrastructureHandlerAssembly(_settings(tmp_path, _ACTIVE)).build()

    assert len(bundles) == 1
    assert isinstance(bundles[0], LangfuseHandler)


def test_build_does_not_register(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Build is deliberately side-effect free on any registry: the composing
    # factory owns registration. Building must not touch the client either.
    calls: list[Any] = []
    monkeypatch.setattr(
        "milky_frog.handlers.langfuse.Langfuse", lambda **kwargs: calls.append(kwargs) or object()
    )

    InfrastructureHandlerAssembly(_settings(tmp_path, _ACTIVE)).build()

    assert len(calls) == 1  # constructed once, nothing else
