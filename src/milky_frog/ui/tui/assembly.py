from __future__ import annotations

from milky_frog.ui.tui.presentation import Emit, TuiPresentationHandler


def tui_presentation_bundle(emit: Emit) -> TuiPresentationHandler:
    """Build the TUI lifecycle bundle for ``MilkyFrog.from_settings(..., bundles=[...])``.

    Presentation is not listed in ``handlers.default_handlers`` (no ``ui/`` import
    there), but it registers on the same dispatcher via the runtime ``extra``
    seam — alongside checkpointing, policy, and observability.
    """
    return TuiPresentationHandler(emit)
