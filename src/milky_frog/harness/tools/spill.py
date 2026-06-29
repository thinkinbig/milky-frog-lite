from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from milky_frog.project import PROJECT_DIRNAME

_OUTPUT_SUBDIR = "tool-output"
_MAX_SPILL_FILES = 100


def spill_full_output(workspace: Path, label: str, text: str) -> str | None:
    """Persist the full *text* under the Workspace and return its relative path.

    Truncation drops the middle of an oversized tool result; spilling keeps the
    whole thing on disk so the model can retrieve it with ``read_file`` instead
    of re-running the tool. Returns a workspace-relative POSIX path, or ``None``
    if the write fails (callers fall back to a plain truncation notice). Older
    spill files beyond ``_MAX_SPILL_FILES`` are pruned to bound disk use.
    """
    out_dir = workspace / PROJECT_DIRNAME / _OUTPUT_SUBDIR
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"{stamp}_{label}_{uuid4().hex[:8]}.txt"
        (out_dir / name).write_text(text, encoding="utf-8")
        _prune_old_spills(out_dir)
    except OSError:
        return None
    return (Path(PROJECT_DIRNAME) / _OUTPUT_SUBDIR / name).as_posix()


def _prune_old_spills(out_dir: Path) -> None:
    """Keep only the most recent ``_MAX_SPILL_FILES`` spill files."""
    files = sorted(out_dir.glob("*.txt"), key=os.path.getmtime)
    for stale in files[:-_MAX_SPILL_FILES]:
        stale.unlink(missing_ok=True)
