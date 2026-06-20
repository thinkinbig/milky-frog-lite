from __future__ import annotations

from milky_frog.handlers import BaseHandler, HandlerRegistry, InfrastructureHandlerAssembly
from milky_frog.settings import Settings
from milky_frog.ui.handlers import StreamingHandlers
from milky_frog.ui.streaming import StreamingPrinter


class HandlerFactory:
    """Composes every Handler bundle for a session in one place.

    The composition root: it knows both the presentation layer (the streaming
    UI Handlers, which need a live ``StreamingPrinter``) and the settings-driven
    infrastructure Handlers (observability, and later persistence/authorization).
    ``build`` wires them all onto one registry and returns the bundles so the
    runtime can release their resources via ``aclose`` at session end.
    """

    def __init__(self, settings: Settings, printer: StreamingPrinter) -> None:
        self._settings = settings
        self._printer = printer

    def build(self) -> tuple[HandlerRegistry, list[BaseHandler]]:
        registry = HandlerRegistry()
        bundles = self._build_bundles()
        for bundle in bundles:
            bundle.register(registry)
        return registry, bundles

    def _build_bundles(self) -> list[BaseHandler]:
        return [
            StreamingHandlers(self._printer),
            *InfrastructureHandlerAssembly(self._settings).build(),
        ]
