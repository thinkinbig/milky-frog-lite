from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HandlerContext:
    """Framework-managed dependencies passed to every handler at notify time."""
