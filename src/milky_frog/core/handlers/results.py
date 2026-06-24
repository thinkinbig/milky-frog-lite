from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SystemPromptSection:
    """Return from a ``RunBeforeStart`` handler to inject content into the system prompt.

    Sections are appended after the base system prompt in registration order.
    """

    content: str


type HandlerResult = SystemPromptSection
"""Control return a handler may emit at a ``RunBefore*`` seam.

A handler that returns ``None`` is pure observation; returning a
``HandlerResult`` signals intent to extend the current step.
"""
