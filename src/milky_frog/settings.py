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
        home = Path(os.environ.get("MILKY_FROG_HOME", Path.home() / ".milky-frog"))
        return cls(
            home=home.expanduser(),
            api_key=os.environ.get("MILKY_FROG_API_KEY"),
            base_url=os.environ.get("MILKY_FROG_BASE_URL"),
            model=os.environ.get("MILKY_FROG_MODEL"),
        )
