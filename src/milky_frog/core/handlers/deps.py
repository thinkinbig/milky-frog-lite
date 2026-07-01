from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HandlerDeps:
    """Stable framework dependencies passed to every Handler.

    Per-Run facts belong on lifecycle signals such as ``RunBeforeModel``. This
    object is reserved for cross-event dependencies that should not be embedded
    in each signal payload.
    """
