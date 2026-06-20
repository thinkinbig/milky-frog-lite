from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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
        values = _load_dotenv(Path.cwd() / ".env")
        home = Path(_get("MILKY_FROG_HOME", values) or (Path.home() / ".milky-frog"))
        return cls(
            home=home.expanduser(),
            api_key=_get("MILKY_FROG_API_KEY", values),
            base_url=_get("MILKY_FROG_BASE_URL", values),
            model=_get("MILKY_FROG_MODEL", values),
        )


def _get(key: str, dotenv: dict[str, str]) -> str | None:
    """Resolve a setting, preferring the real environment over the .env file."""
    return os.environ.get(key) or dotenv.get(key)


def _load_dotenv(path: Path) -> dict[str, str]:
    """Parse a ``.env`` file into a mapping; missing files yield an empty dict.

    Supports ``KEY=value`` lines, ``export KEY=value``, ``#`` comments, blank
    lines, and surrounding single/double quotes. Lines that do not parse are
    skipped rather than raising, so a malformed entry never blocks startup.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].lstrip()
        key, sep, value = stripped.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values
