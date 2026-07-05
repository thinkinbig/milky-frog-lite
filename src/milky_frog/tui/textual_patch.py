"""Workaround for Textual stdin UTF-8 strict decode crashes on paste.

See https://github.com/Textualize/textual/issues/6456 — terminal paste (especially
CJK in Cursor's integrated terminal) can deliver truncated or non-UTF-8 bytes;
Textual's input thread uses ``errors="strict"`` and panics. Remove this module
once a fixed Textual release is our minimum dependency.
"""

from __future__ import annotations

from codecs import getincrementaldecoder as _orig_getincrementaldecoder
from typing import Any

_DRIVER_MODULES = (
    "textual.drivers.linux_driver",
    "textual.drivers.linux_inline_driver",
    "textual.drivers.web_driver",
)

_PATCHED = False


def _patched_getincrementaldecoder(encoding: str) -> Any:
    decoder_cls = _orig_getincrementaldecoder(encoding)
    if encoding != "utf-8":
        return decoder_cls

    class _Utf8TolerantIncrementalDecoder(decoder_cls):  # type: ignore[misc,valid-type]
        def __init__(self, errors: str = "strict") -> None:
            super().__init__("replace" if errors == "strict" else errors)

    return _Utf8TolerantIncrementalDecoder


def patch_textual_utf8_decode() -> None:
    """Patch Textual driver modules to decode stdin with ``errors='replace'``."""
    global _PATCHED
    if _PATCHED:
        return

    import importlib

    for module_name in _DRIVER_MODULES:
        module = importlib.import_module(module_name)
        module.getincrementaldecoder = _patched_getincrementaldecoder  # type: ignore[attr-defined]

    _PATCHED = True
