"""Handler bus types — notify-time dependencies.

Control-return types (``Compacted``, ``HandlerResult``) live in ``domain``: the
loop applies them, and Handlers that produce them import from ``domain`` without
depending on ``core``.
"""

from milky_frog.core.handlers.deps import HandlerDeps

__all__ = [
    "HandlerDeps",
]
