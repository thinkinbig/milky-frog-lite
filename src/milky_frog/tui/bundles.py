from __future__ import annotations

from milky_frog.events.hub import Handler
from milky_frog.tui.bash_render import BashRenderHandler
from milky_frog.tui.presentation import Emit, TuiPresentationHandler


def make_tui_presentation_handlers(emit: Emit) -> list[Handler]:
    """Build the TUI lifecycle handlers for ``AgentSession(..., bundles=[...])``.

    Returns two handlers registered on the shared hub alongside
    checkpointing, policy, and observability:
    - ``TuiPresentationHandler`` — maps all lifecycle signals to Textual messages.
    - ``BashRenderHandler`` — routes bash results to command-specific messages.
    """
    return [TuiPresentationHandler(emit), BashRenderHandler(emit)]
