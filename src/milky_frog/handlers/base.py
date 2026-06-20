from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class BaseEvent(BaseModel):
    """Base type for Harness lifecycle events dispatched through HandlerRegistry.

    Events are mutable Pydantic models (not frozen dataclasses) so intercept
    Handlers can apply ``TransformContext`` and ``PatchToolResult`` outcomes
    in place before observe Handlers run.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str
