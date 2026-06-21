from __future__ import annotations

from milky_frog.handlers import BaseHandler, HandlerRegistry, LangfuseHandler
from milky_frog.settings import Settings
from milky_frog.ui.handlers import StreamingHandlers
from milky_frog.ui.streaming import StreamingPrinter


class HandlerFactory:
    """Composes every Handler bundle for a session in one place.

    Builds the streaming UI Handlers (which need a live ``StreamingPrinter``)
    and the settings-driven Langfuse Handler, wires them onto one registry,
    and returns the bundles so the runtime can release their resources
    via ``aclose`` at session end.
    """

    def __init__(self, settings: Settings, printer: StreamingPrinter) -> None:
        self._settings = settings
        self._printer = printer

    def build(self) -> tuple[HandlerRegistry, list[BaseHandler]]:
        registry = HandlerRegistry()
        bundles: list[BaseHandler] = [StreamingHandlers(self._printer)]
        langfuse = LangfuseHandler.from_settings(self._settings)
        if langfuse is not None:
            bundles.append(langfuse)
        for bundle in bundles:
            bundle.register(registry)
        return registry, bundles
