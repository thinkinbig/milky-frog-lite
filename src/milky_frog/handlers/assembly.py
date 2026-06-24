from __future__ import annotations

from collections.abc import Sequence

from milky_frog.checkpoint import CheckpointStore
from milky_frog.handlers.budget import BudgetHandler
from milky_frog.handlers.checkpoint import CheckpointHandler
from milky_frog.handlers.dispatcher import BaseHandler
from milky_frog.handlers.policy import PolicyHandler
from milky_frog.handlers.skills import AgentContextHandler
from milky_frog.infra.observability.langfuse import LangfuseHandler
from milky_frog.settings import Settings


def default_handlers(
    settings: Settings,
    checkpoints: CheckpointStore,
    *,
    model_name: str = "",
    extra: Sequence[BaseHandler] = (),
) -> list[BaseHandler]:
    """Assemble every lifecycle handler bundle for a session, in one place.

    Returns the bundles in registration order. ``CheckpointHandler`` declares
    its own priority (100) so it always persists before other observers
    regardless of list position. The caller registers each bundle on the
    dispatcher and owns their lifetime — every returned bundle is entered on
    session open and released when the runtime closes the session.
    """
    bundles: list[BaseHandler] = [
        CheckpointHandler(checkpoints),
        PolicyHandler(),
        AgentContextHandler(settings.home),
        BudgetHandler(model_name),
    ]
    bundles.extend(extra)
    langfuse = LangfuseHandler.from_settings(settings)
    if langfuse is not None:
        bundles.append(langfuse)
    return bundles
