from __future__ import annotations

from pathlib import Path

from milky_frog.handlers.context import HandlerContext, SystemPromptSection
from milky_frog.handlers.dispatcher import BaseHandler, EventDispatcher
from milky_frog.handlers.events import RunBeforeStart
from milky_frog.harness.prompt import agent_context_section


class AgentContextHandler(BaseHandler):
    """Inject agent-home context into the system prompt via ``RunBeforeStart``.

    Reads global and workspace instructions, append rules, and skill catalog
    metadata from ``home`` (``Settings.home`` at assembly time). The model is
    pointed at skill locations rather than receiving full skill bodies inline.

    Wiring (in ``default_handlers``):
        AgentContextHandler(settings.home).register(bus)
    """

    def __init__(self, home: Path) -> None:
        self._home = home

    def register(self, registry: EventDispatcher) -> None:
        registry.on(RunBeforeStart)(self._on_before_start)

    async def _on_before_start(
        self, event: RunBeforeStart, ctx: HandlerContext | None = None
    ) -> SystemPromptSection | None:
        content = agent_context_section(event.workspace, self._home)
        if content is None:
            return None
        return SystemPromptSection(content)
