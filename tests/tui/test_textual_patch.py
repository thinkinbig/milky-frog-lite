from __future__ import annotations

from milky_frog.tui.textual_patch import (
    _patched_getincrementaldecoder,
    patch_textual_utf8_decode,
)


def test_patched_decoder_replaces_truncated_multibyte_sequences() -> None:
    decode = _patched_getincrementaldecoder("utf-8")().decode
    assert decode(b"\xe4", final=True) == "\ufffd"


def test_patched_decoder_preserves_valid_utf8() -> None:
    decode = _patched_getincrementaldecoder("utf-8")().decode
    assert decode("你好".encode(), final=True) == "你好"


def test_patch_textual_utf8_decode_updates_driver_modules() -> None:
    patch_textual_utf8_decode()

    import textual.drivers.linux_driver as linux_driver

    decode = linux_driver.getincrementaldecoder("utf-8")().decode  # type: ignore[attr-defined]
    assert decode(b"\xe4", final=True) == "\ufffd"
