from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class BaseEvent(BaseModel):
    """Base type for ephemeral Harness lifecycle signals delivered via ``notify``.

    These are not Checkpoint events — they exist only for live UI and
    observability Handlers during a Run.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    run_id: str
