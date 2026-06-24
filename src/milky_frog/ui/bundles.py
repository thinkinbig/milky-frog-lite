from __future__ import annotations

from milky_frog.events.hub import BaseHandler
from milky_frog.ui.bash_render import BashRenderHandler
from milky_frog.ui.presentation import Emit, TuiPresentationHandler


def tui_presentation_bundle(emit: Emit) -> list[BaseHandler]:
    """Build the TUI lifecycle handlers for ``AgentSession(..., bundles=[...])``.

    Returns two handlers registered on the shared hub alongside
    checkpointing, policy, and observability:
    - ``TuiPresentationHandler`` — maps all lifecycle signals to Textual messages.
    - ``BashRenderHandler`` — routes bash results to command-specific messages.
    """
    return [TuiPresentationHandler(emit), BashRenderHandler(emit)]
