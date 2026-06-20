from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class BaseEvent(BaseModel):
    """Base type for Harness lifecycle events dispatched through HandlerRegistry."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str
