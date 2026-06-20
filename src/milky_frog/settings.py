from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

_LANGFUSE_HOST_DEFAULT = "https://cloud.langfuse.com"


@dataclass(frozen=True, slots=True)
class LangfuseSettings:
    enabled: bool
    public_key: str | None
    secret_key: str | None
    host: str

    @property
    def active(self) -> bool:
        return self.enabled and bool(self.public_key and self.secret_key)


@dataclass(frozen=True, slots=True)
class Settings:
    home: Path
    api_key: str | None
    base_url: str | None
    model: str | None
    langfuse: LangfuseSettings

    @property
    def database_path(self) -> Path:
        return self.home / "state.db"

    @classmethod
    def from_environment(cls) -> Settings:
        values = dotenv_values(Path.cwd() / ".env")
        toggles = _load_feature_toggles(Path.cwd() / "milky-frog.json")
        home = Path(_get("MILKY_FROG_HOME", values) or (Path.home() / ".milky-frog"))
        home = home.expanduser()
        return cls(
            home=home,
            api_key=_get("MILKY_FROG_API_KEY", values),
            base_url=_get("MILKY_FROG_BASE_URL", values),
            model=_get("MILKY_FROG_MODEL", values),
            langfuse=_load_langfuse_settings(values, toggles),
        )


def _load_feature_toggles(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _load_langfuse_settings(
    values: Mapping[str, str | None], toggles: dict[str, object]
) -> LangfuseSettings:
    section = toggles.get("langfuse")
    enabled = bool(section.get("enabled", False)) if isinstance(section, dict) else False
    return LangfuseSettings(
        enabled=enabled,
        public_key=_get("LANGFUSE_PUBLIC_KEY", values),
        secret_key=_get("LANGFUSE_SECRET_KEY", values),
        host=_get("LANGFUSE_BASE_URL", values) or _LANGFUSE_HOST_DEFAULT,
    )


def _get(key: str, dotenv: Mapping[str, str | None]) -> str | None:
    """Resolve a setting, preferring the real environment over the .env file.

    A variable present in the real environment wins even when set to an empty
    string, so an explicit empty override falls back to defaults instead of
    being silently replaced by a stale ``.env`` value.
    """
    value = os.environ[key] if key in os.environ else dotenv.get(key)
    return value or None
