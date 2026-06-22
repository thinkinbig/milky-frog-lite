from __future__ import annotations

from rich.text import Text

# Truecolor palette sampled to match the project mascot (assets/milky-frog.png):
# a laughing Trump-frog — blonde comb-over, golden-amber skin, green eye
# patches, a wide open mouth with a pink tongue, white collar, red tie, navy
# suit, and light-blue laughing-to-tears droplets. Rich downsamples the hex
# colors automatically on terminals without truecolor support.
_PALETTE = {
    "Y": ("██", "#e6c84b"),  # blonde swept-over hair
    "S": ("██", "#e8a23c"),  # golden-amber frog skin
    "G": ("██", "#6fa83c"),  # green eye patches
    "K": ("██", "#1a1a1a"),  # pupils / open-mouth rim
    "M": ("██", "#4a1e12"),  # open mouth interior
    "P": ("██", "#e8497f"),  # pink tongue
    "W": ("██", "#f4f2ec"),  # white shirt collar
    "R": ("██", "#ce382e"),  # the long red tie
    "N": ("██", "#2b3a6b"),  # navy suit
    "T": ("░░", "#5fb0e2"),  # laughing-to-tears droplets
}

_FROG = (
    "   YYYYYYYY   ",
    "  YYYYYYYYYY  ",
    " YYYYYYYYYYYY ",
    " GGGSSSSSSGGG ",
    "TGKGSSSSSSGKGT",
    "T SSSSSSSSSS T",
    "  SKKKKKKKKS  ",
    "  SKMMMMMMKS  ",
    "  SKMMPPMMKS  ",
    "  SSKKKKKKSS  ",
    "  SWWWRRWWWS  ",
    " NNWWWRRWWWNN ",
    " NNNNNRRNNNNN ",
    "NNNNNNRRNNNNNN",
    "NNNNNNNNNNNNNN",
    " NNNNNNNNNNNN ",
)


def pixel_frog_logo() -> Text:
    """Return a compact truecolor pixel interpretation of the project mascot."""
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
