from __future__ import annotations

from rich.text import Text

_PALETTE = {
    "Y": ("██", "bright_yellow"),
    "W": ("▓▓", "bright_white"),
    "G": ("▒▒", "bright_green"),
    "K": ("  ", "black"),
    "R": ("▄▄", "bright_red"),
    "B": ("░░", "blue"),
    "C": ("░░", "bright_cyan"),
}

_FROG = (
    "    YYYYYY    ",
    "  YYYYYYYYYY  ",
    "CYYYGGYYGGYYYC",
    "CYYYGKYYKGYYYC",
    " YYYKKKKKKYYY ",
    " YYYYKRRKYYYY ",
    "   WWYYYYWW   ",
    "  BBWWRRWWBB  ",
    " BBBBBRRBBBBB ",
)


def pixel_frog_logo() -> Text:
    """Return a compact ANSI-safe pixel interpretation of the project mascot."""
    logo = Text()
    for row_index, row in enumerate(_FROG):
        for pixel in row:
            if pixel == " ":
                logo.append("  ")
            else:
                glyph, style = _PALETTE[pixel]
                logo.append(glyph, style=style)
        if row_index < len(_FROG) - 1:
            logo.append("\n")
    return logo
