from __future__ import annotations

from collections.abc import Callable

from milky_frog.handlers.langfuse import build_langfuse_handler
from milky_frog.handlers.registry import BaseHandler
from milky_frog.settings import Settings

HandlerProvider = Callable[[Settings], BaseHandler | None]

# The roster of settings-driven infrastructure Handlers. Each entry is a
# provider that builds its bundle (or returns None when inactive); adding a
# Handler means a new provider in its own file plus one line here.
HANDLER_PROVIDERS: tuple[HandlerProvider, ...] = (build_langfuse_handler,)


def build_infrastructure_handlers(settings: Settings) -> list[BaseHandler]:
    """Build every active settings-driven infrastructure Handler bundle.

    Returns the bundles unregistered; the composing factory registers them
    alongside the UI Handlers so the whole roster is wired in one place.
    """
    return [
        handler for provider in HANDLER_PROVIDERS if (handler := provider(settings)) is not None
    ]
