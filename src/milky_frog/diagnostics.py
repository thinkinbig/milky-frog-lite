from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CheckStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One doctor finding: a named check, its status, and the value it observed.

    The doctor command builds these; the Terminal UI only renders them. Keeping the
    vocabulary here (not in the renderer) stops the UI module from owning domain values.
    """

    name: str
    status: CheckStatus
    value: str
