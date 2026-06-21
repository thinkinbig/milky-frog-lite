from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from pydantic import JsonValue

from milky_frog.checkpoint.base import RunClaimError, StoredRun
from milky_frog.checkpoint.events import RunEvent, dump_checkpoint_body
from milky_frog.domain import RunStatus


class SqliteCheckpointStore:
    """SQLite adapter for the append-only Checkpoint seam."""

    def __init__(self, path: Path) -> None:
        self._path = path.resolve()
        self._lock_path = self._path.with_name(f"{self._path.name}.locks")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def claim(self, run_id: str) -> Iterator[None]:
        """Hold the OS-level ownership lock for one Run.

        The lock is released automatically if the process exits, which makes an
        active SQLite row resumable after a crash without allowing a second live
        process to advance the same Run concurrently.
        """
        name = sha256(run_id.encode()).hexdigest()
        path = self._lock_path / name
        with path.open("a+b") as handle:
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            try:
                self._lock(handle.fileno())
            except OSError as error:
                raise RunClaimError(f"Run {run_id} is already active") from error
            try:
                yield
            finally:
                self._unlock(handle.fileno())

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
            persisted = self._append(connection, run_id, event, now)
            connection.execute(
                "UPDATE runs SET status = COALESCE(?, status), updated_at = ? WHERE run_id = ?",
                (status, now.isoformat(), run_id),
            )
        return persisted

    def events(self, run_id: str) -> tuple[RunEvent, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT sequence, event_type, version, payload, created_at "
                "FROM run_events WHERE run_id = ? ORDER BY sequence",
                (run_id,),
            ).fetchall()
        return tuple(
            RunEvent.from_parts(
                event_type=str(row["event_type"]),
                payload=self._load_payload(str(row["payload"])),
                version=int(row["version"]),
                sequence=int(row["sequence"]),
                created_at=datetime.fromisoformat(str(row["created_at"])),
            )
            for row in rows
        )

    def prepare_resume(
        self,
        run_id: str,
        expected_updated_at: datetime,
        events: tuple[RunEvent, ...] = (),
    ) -> tuple[RunEvent, ...]:
        """Atomically persist resume seeds and project the Run as active."""
        now = datetime.now(UTC)
        with self._connect() as connection:
            updated = connection.execute(
                "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ? AND updated_at = ?",
                (
                    RunStatus.RUNNING,
                    now.isoformat(),
                    run_id,
                    expected_updated_at.isoformat(),
                ),
            ).rowcount
            if updated == 0:
                raise RuntimeError(f"Run {run_id} changed while resume was being prepared")
            return tuple(self._append(connection, run_id, event, now) for event in events)

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

    @staticmethod
    def _append(
        connection: sqlite3.Connection,
        run_id: str,
        event: RunEvent,
        now: datetime,
    ) -> RunEvent:
        row = connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence "
            "FROM run_events WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("failed to allocate event sequence")
        sequence = int(row["next_sequence"])
        event_type, payload = dump_checkpoint_body(event.body)
        connection.execute(
            "INSERT INTO run_events("
            "run_id, sequence, event_type, version, payload, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (
                run_id,
                sequence,
                event_type,
                event.version,
                json.dumps(payload, ensure_ascii=False),
                now.isoformat(),
            ),
        )
        return RunEvent(
            body=event.body,
            version=event.version,
            sequence=sequence,
            created_at=now,
        )

    @staticmethod
    def _lock(file_descriptor: int) -> None:
        if os.name == "nt":
            import msvcrt

            os.lseek(file_descriptor, 0, os.SEEK_SET)
            msvcrt.locking(file_descriptor, msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            return
        import fcntl

        fcntl.flock(file_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(file_descriptor: int) -> None:
        if os.name == "nt":
            import msvcrt

            os.lseek(file_descriptor, 0, os.SEEK_SET)
            msvcrt.locking(file_descriptor, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
            return
        import fcntl

        fcntl.flock(file_descriptor, fcntl.LOCK_UN)
