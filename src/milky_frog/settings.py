from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values


@dataclass(frozen=True, slots=True)
class Settings:
    home: Path
    api_key: str | None
    base_url: str | None
    model: str | None

    @property
    def database_path(self) -> Path:
        return self.home / "state.db"

    @classmethod
    def from_environment(cls) -> Settings:
        values = dotenv_values(Path.cwd() / ".env")
        home = Path(_get("MILKY_FROG_HOME", values) or (Path.home() / ".milky-frog"))
        return cls(
            home=home.expanduser(),
            api_key=_get("MILKY_FROG_API_KEY", values),
            base_url=_get("MILKY_FROG_BASE_URL", values),
            model=_get("MILKY_FROG_MODEL", values),
        )


def _get(key: str, dotenv: Mapping[str, str | None]) -> str | None:
    """Resolve a setting, preferring the real environment over the .env file.

    A variable present in the real environment wins even when set to an empty
    string, so an explicit empty override falls back to defaults instead of
    being silently replaced by a stale ``.env`` value.
    """
    value = os.environ[key] if key in os.environ else dotenv.get(key)
    return value or None
