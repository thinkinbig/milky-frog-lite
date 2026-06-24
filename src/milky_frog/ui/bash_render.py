from __future__ import annotations

import re
from collections.abc import Callable

from textual.message import Message

from milky_frog.handlers.context import HandlerContext
from milky_frog.handlers.events import RunAfterTool
from milky_frog.handlers.hub import BaseHandler, EventHub
from milky_frog.ui.messages import BashOutputMsg, GitOutputMsg, GrepOutputMsg

Emit = Callable[[Message], object]

_GIT_RE = re.compile(r"^\s*git(\s|$)")
_GREP_RE = re.compile(r"^\s*(grep|rg|ripgrep)(\s|$)")


class BashRenderHandler(BaseHandler):
    """Routes bash RunAfterTool results to command-specific Textual messages.

    Observes RunAfterTool alongside TuiPresentationHandler; only fires for
    bash tool calls (all others are ignored). Each subcommand family gets its
    own Textual message so app.py handlers can render them independently.
    """

    def __init__(self, emit: Emit) -> None:
        self._emit = emit

    def register(self, hub: EventHub) -> None:
        hub.on(RunAfterTool)(self._on_after_tool)

    async def _on_after_tool(self, event: RunAfterTool, ctx: HandlerContext | None = None) -> None:
        if event.call.name != "bash":
            return
        command = str(event.call.arguments.get("command", ""))
        content = event.result.content
        is_error = event.result.is_error

        if _GIT_RE.match(command):
            self._emit(GitOutputMsg(command, content=content, is_error=is_error))
        elif _GREP_RE.match(command):
            self._emit(GrepOutputMsg(command, content=content, is_error=is_error))
        else:
            self._emit(BashOutputMsg(content=content, is_error=is_error))
