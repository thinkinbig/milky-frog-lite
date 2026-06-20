from __future__ import annotations

from rich.text import Text

_PALETTE = {
    "Y": ("██", "bright_yellow"),  # signature blonde comb-over
    "S": ("██", "yellow"),  # golden frog skin
    "G": ("▒▒", "bright_green"),  # green eye patches
    "K": ("██", "black"),  # pupils / open-mouth rim
    "M": ("▓▓", "red"),  # open mouth interior
    "W": ("▓▓", "bright_white"),  # shirt collar
    "R": ("██", "bright_red"),  # the long red tie
    "N": ("██", "blue"),  # navy suit
    "T": ("░░", "bright_cyan"),  # laughing-to-tears droplets
}

# A Trump-frog: blonde swept hair up top, golden-yellow skin with green eyes,
# laughing-to-tears droplets, a wide open mouth, white collar, long red tie
# and a navy suit below.
_FROG = (
    "   YYYYYYYY   ",
    "  YYYYYYYYYY  ",
    " YYYYYYYYYYYY ",
    " GGGSSSSSSGGG ",
    "TGKGSSSSSSGKGT",
    "T SSSSSSSSSS T",
    "  SKKKKKKKKS  ",
    "  SKMMMMMMKS  ",
    "  SKMMMMMMKS  ",
    "  SSKKKKKKSS  ",
    "  SWWWRRWWWS  ",
    " NNWWWRRWWWNN ",
    " NNNNNRRNNNNN ",
    "NNNNNNRRNNNNNN",
    "NNNNNNNNNNNNNN",
    " NNNNNNNNNNNN ",
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
