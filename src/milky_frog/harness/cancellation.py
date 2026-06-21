from __future__ import annotations

from milky_frog.domain import RunCancellation


class ToolRunCancelled(Exception):
    """Cooperative cancel arrived while a Tool was executing."""


def is_cancelled(cancellation: RunCancellation | None) -> bool:
    return cancellation is not None and cancellation.is_cancelled
