from __future__ import annotations

from dataclasses import dataclass

from milky_frog.domain import CompactionState


@dataclass(frozen=True, slots=True)
class Compacted:
    """A ``RunBeforeModel`` handler's request to replace the transcript prefix.

    The first activated control return: a handler (e.g. ``CompactionHandler``)
    returns this from ``before_model`` and the loop folds ``compaction`` into the
    ``RunState`` before assembling the model request.
    """

    compaction: CompactionState


type HandlerResult = Compacted
"""Per-step control returns a Handler may hand back to the loop.

The event bus collects non-``None`` returns from ``broadcast`` and the loop acts
on them. Today the only variant is ``Compacted`` (``RunBeforeModel``); more may
be added (e.g. tool authorization for ``RunBeforeTool``) as a union.
"""
