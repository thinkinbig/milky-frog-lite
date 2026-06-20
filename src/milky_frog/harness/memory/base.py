from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    key: str
    content: str


class MemoryStore(Protocol):
    def list(self, workspace_id: str) -> tuple[MemoryEntry, ...]: ...

    def set(self, workspace_id: str, entry: MemoryEntry) -> None: ...

    def delete(self, workspace_id: str, key: str) -> bool: ...
