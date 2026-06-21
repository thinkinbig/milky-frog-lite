from __future__ import annotations

from typing import Protocol

from milky_frog.handlers.registry import BaseHandler
from milky_frog.infra.observability.langfuse import LangfuseHandler
from milky_frog.settings import Settings


class SettingsDrivenHandler(Protocol):
    @classmethod
    def from_settings(cls, settings: Settings) -> BaseHandler | None: ...


class InfrastructureHandlerAssembly:
    """Build every active settings-driven infrastructure Handler bundle.

    Returns the bundles unregistered; the composing factory registers them
    alongside the UI Handlers so the whole roster is wired in one place.
    """

    # The roster of settings-driven infrastructure Handlers. Each entry is a
    # Handler type that builds its bundle (or returns None when inactive);
    # adding a Handler means a new type in its own file plus one line here.
    _ROSTER: tuple[type[SettingsDrivenHandler], ...] = (LangfuseHandler,)

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def build(self) -> list[BaseHandler]:
        return [
            handler
            for handler_type in self._ROSTER
            if (handler := handler_type.from_settings(self._settings)) is not None
        ]
