from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from pydantic import JsonValue

from milky_frog.checkpoint.base import RunEvent, StoredRun
from milky_frog.domain import RunStatus


class SqliteCheckpointStore:
    """SQLite adapter for the append-only Checkpoint seam."""

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    workspace TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS run_events (
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, sequence)
                );
                """
            )

    def create_run(self, run_id: str, workspace: Path) -> StoredRun:
        now = datetime.now(UTC)
        resolved = workspace.resolve()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO runs(run_id, workspace, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (run_id, str(resolved), RunStatus.RUNNING, now.isoformat(), now.isoformat()),
            )
        return StoredRun(run_id, resolved, RunStatus.RUNNING, now, now)

    def append(self, run_id: str, event: RunEvent, status: RunStatus | None = None) -> RunEvent:
        now = datetime.now(UTC)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence "
                "FROM run_events WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("failed to allocate event sequence")
            sequence = int(row["next_sequence"])
            connection.execute(
                "INSERT INTO run_events("
                "run_id, sequence, event_type, version, payload, created_at"
                ") "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    sequence,
                    event.event_type,
                    event.version,
                    json.dumps(event.payload, ensure_ascii=False),
                    now.isoformat(),
                ),
            )
            connection.execute(
                "UPDATE runs SET status = COALESCE(?, status), updated_at = ? WHERE run_id = ?",
                (status, now.isoformat(), run_id),
            )
        return RunEvent(event.event_type, event.payload, event.version, sequence, now)

    def events(self, run_id: str) -> tuple[RunEvent, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT sequence, event_type, version, payload, created_at "
                "FROM run_events WHERE run_id = ? ORDER BY sequence",
                (run_id,),
            ).fetchall()
        return tuple(
            RunEvent(
                event_type=str(row["event_type"]),
                payload=self._load_payload(str(row["payload"])),
                version=int(row["version"]),
                sequence=int(row["sequence"]),
                created_at=datetime.fromisoformat(str(row["created_at"])),
            )
            for row in rows
        )

    def get_run(self, run_id: str) -> StoredRun | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return None if row is None else self._stored_run(row)

    def list_runs(self, limit: int = 20) -> tuple[StoredRun, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM runs ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return tuple(self._stored_run(row) for row in rows)

    @staticmethod
    def _stored_run(row: sqlite3.Row) -> StoredRun:
        return StoredRun(
            run_id=str(row["run_id"]),
            workspace=Path(str(row["workspace"])),
            status=RunStatus(str(row["status"])),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    @staticmethod
    def _load_payload(value: str) -> dict[str, JsonValue]:
        loaded = json.loads(value)
        if not isinstance(loaded, dict):
            raise ValueError("checkpoint event payload must be an object")
        return {str(key): item for key, item in loaded.items()}
