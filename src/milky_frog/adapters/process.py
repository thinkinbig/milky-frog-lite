"""Post-processing shared by every Sandbox command runner.

Both ``LocalSandbox`` (host subprocess) and ``DockerSandbox`` (``docker exec``)
capture raw bytes with stderr merged into stdout, then turn them into the same
``CommandResult`` shape. Keeping the decode / newline-normalization / ANSI-strip
rules here means the two adapters cannot drift in what a Tool observes.
"""

from __future__ import annotations

import re

from milky_frog.core.sandbox import CommandResult

_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\a]*(?:\a|\x1b\\))")

_PRESENTATION_ENV: dict[str, str] = {
    "COLORTERM": "truecolor",
    "CLICOLOR_FORCE": "1",
    "FORCE_COLOR": "1",
}

_GIT_COLOR_ENV: dict[str, str] = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "color.ui",
    "GIT_CONFIG_VALUE_0": "always",
}


def with_presentation_env(env: dict[str, str]) -> dict[str, str]:
    """Enrich *env* so child processes emit colour a Terminal UI can render."""
    enriched = {**env, **_PRESENTATION_ENV}
    enriched.setdefault("TERM", "xterm-256color")
    if "GIT_CONFIG_COUNT" not in enriched:
        enriched.update(_GIT_COLOR_ENV)
    return enriched


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def make_command_result(exit_code: int, raw: bytes) -> CommandResult:
    """Decode captured bytes into the model-facing and display-facing texts.

    ``output`` is ANSI-stripped (what the model reads); ``display_output`` keeps
    the escape codes and is ``None`` when stripping changed nothing.
    """
    display_text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    text = strip_ansi(display_text)
    display = display_text if display_text != text else None
    return CommandResult(exit_code=exit_code, output=text, display_output=display)
