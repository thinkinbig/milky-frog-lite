from rich.console import Console

console = Console()
error_console = Console(stderr=True)


def get_box_width() -> int:
    """Terminal width capped at 120, read at render time so resize is respected."""
    w = console.width
    return min(w, 120) if w > 0 else 92


# Alias for callers that need a value at import time (prompt_toolkit Frame sizing).
BOX_WIDTH = get_box_width()
