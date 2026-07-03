from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from milky_frog.domain.provider import Provider, infer_provider

logger = logging.getLogger(__name__)


class LangfuseSettings(BaseModel):
    """Langfuse observability configuration."""

    enabled: bool = False
    public_key: str | None = None
    secret_key: str | None = None
    host: str = "https://cloud.langfuse.com"
    flush_timeout_seconds: float = Field(default=10.0, ge=1.0)

    @property
    def active(self) -> bool:
        return self.enabled and bool(self.public_key and self.secret_key)


class Settings(BaseSettings):
    """Application settings sourced from environment variables and ``.env`` file.

    ``MILKY_FROG_*`` variables are read automatically by pydantic via
    ``validation_alias``.  Langfuse uses a separate ``LANGFUSE_*`` prefix
    with its own aliases.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="MILKY_FROG_",
        extra="ignore",
        populate_by_name=True,
    )

    api_key: str | None = Field(
        default=None,
        validation_alias="MILKY_FROG_API_KEY",
    )
    model: str | None = Field(
        default=None,
        validation_alias="MILKY_FROG_MODEL",
    )
    base_url: str | None = Field(
        default=None,
        validation_alias="MILKY_FROG_BASE_URL",
    )
    provider: str | None = Field(
        default=None,
        validation_alias="MILKY_FROG_PROVIDER",
    )
    home: Path = Field(
        default=Path.home() / ".milky-frog",
        validation_alias="MILKY_FROG_HOME",
    )
    max_retries: int = Field(
        default=3,
        validation_alias="MILKY_FROG_MAX_RETRIES",
        ge=1,
    )
    retry_base_delay: float = Field(
        default=1.0,
        validation_alias="MILKY_FROG_RETRY_BASE_DELAY",
        ge=0.0,
    )

    # ── Langfuse (separate LANGFUSE_* env namespace) ──────────────
    langfuse_enabled: bool = Field(
        default=False,
        validation_alias="LANGFUSE_ENABLED",
    )
    langfuse_public_key: str | None = Field(
        default=None,
        validation_alias="LANGFUSE_PUBLIC_KEY",
    )
    langfuse_secret_key: str | None = Field(
        default=None,
        validation_alias="LANGFUSE_SECRET_KEY",
    )
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com",
        validation_alias="LANGFUSE_BASE_URL",
    )
    langfuse_flush_timeout_seconds: float = Field(
        default=10.0,
        validation_alias="LANGFUSE_FLUSH_TIMEOUT",
        ge=1.0,
    )

    # ── Validators ────────────────────────────────────────────────

    @field_validator(
        "api_key",
        "model",
        "base_url",
        "provider",
        "langfuse_public_key",
        "langfuse_secret_key",
        mode="before",
    )
    @classmethod
    def _coerce_empty_to_none(cls, v: object) -> object:
        """Convert empty-string env vars to ``None``.

        Matching the legacy behaviour where ``MILKY_FROG_API_KEY=""`` is
        treated as "not configured" rather than a valid empty value.
        """
        if v == "" or v is None:
            return None
        return v

    @field_validator("home", mode="after")
    @classmethod
    def _expand_home(cls, v: Path) -> Path:
        return v.expanduser()

    # ── Derived properties ────────────────────────────────────────

    @property
    def database_path(self) -> Path:
        return self.home / "state.db"

    @property
    def resolved_provider(self) -> Provider:
        """Provider for token counting: explicit ``MILKY_FROG_PROVIDER`` wins,
        otherwise inferred from the model name and base URL.
        """
        if self.provider:
            try:
                return Provider(self.provider.lower())
            except ValueError:
                logger.warning(
                    "unknown MILKY_FROG_PROVIDER %r; inferring from model/base_url",
                    self.provider,
                )
        return infer_provider(self.model, self.base_url)

    @property
    def langfuse(self) -> LangfuseSettings:
        return LangfuseSettings(
            enabled=self.langfuse_enabled,
            public_key=self.langfuse_public_key,
            secret_key=self.langfuse_secret_key,
            host=self.langfuse_host,
            flush_timeout_seconds=self.langfuse_flush_timeout_seconds,
        )

    # ── Factories ─────────────────────────────────────────────────

    @classmethod
    def from_environment(cls) -> Settings:
        """Convenience factory — equivalent to ``Settings()``.

        Kept for backward compatibility with existing call sites.
        """
        return cls()
