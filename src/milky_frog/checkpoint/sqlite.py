from __future__ import annotations

import sqlite3
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path

from milky_frog.checkpoint._locking import RunLock
from milky_frog.checkpoint.base import CleanupScope, RunClaimError, StoredRun
from milky_frog.checkpoint.snapshot import dump_run_state, load_run_state
from milky_frog.domain import RunState, RunStatus


class SqliteCheckpointStore:
    """SQLite adapter for the RunState snapshot Checkpoint seam."""

    def __init__(self, path: Path) -> None:
        self._path = path.resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RunLock(self._path.with_name(f"{self._path.name}.locks"))
        self._initialize()

    def claim(self, run_id: str) -> AbstractContextManager[None]:
        """Hold the OS-level ownership lock for one Run (crash-safe)."""
        return self._lock.claim(run_id)

    def create_run(self, run_id: str, workspace: Path) -> StoredRun:
        now = datetime.now(UTC)
        resolved = workspace.resolve()
        empty = dump_run_state(RunState(run_id=run_id, workspace=resolved))
        with self._db as conn:
            conn.execute(
                "INSERT INTO runs("
                "run_id, workspace, status, state_json, final_message, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, NULL, ?, ?)",
                (run_id, str(resolved), RunStatus.RUNNING, empty, now.isoformat(), now.isoformat()),
            )
        return StoredRun(run_id, resolved, RunStatus.RUNNING, now, now)

    def save_state(
        self,
        run_id: str,
        state: RunState,
        *,
        status: RunStatus | None = None,
        final_message: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        with self._db as conn:
            conn.execute(
                "UPDATE runs SET state_json = ?, status = COALESCE(?, status), "
                "final_message = COALESCE(?, final_message), updated_at = ? WHERE run_id = ?",
                (
                    dump_run_state(state),
                    status,
                    final_message,
                    now.isoformat(),
                    run_id,
                ),
            )

    def load_state(self, run_id: str) -> RunState:
        stored = self.get_run(run_id)
        if stored is None:
            raise LookupError(run_id)
        with self._db as conn:
            row = conn.execute("SELECT state_json FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise LookupError(run_id)
        return load_run_state(run_id, stored.workspace, str(row["state_json"]))

    def prepare_resume(
        self,
        run_id: str,
        expected_updated_at: datetime,
        state: RunState,
    ) -> StoredRun:
        """Atomically CAS the Run back to RUNNING and persist the resume state."""
        now = datetime.now(UTC)
        with self._db as conn:
            updated = conn.execute(
                "UPDATE runs SET status = ?, state_json = ?, updated_at = ? "
                "WHERE run_id = ? AND updated_at = ?",
                (
                    RunStatus.RUNNING,
                    dump_run_state(state),
                    now.isoformat(),
                    run_id,
                    expected_updated_at.isoformat(),
                ),
            ).rowcount
            if updated == 0:
                raise RuntimeError(f"Run {run_id} changed while resume was being prepared")
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise RuntimeError(f"Run {run_id} disappeared while resume was being prepared")
        return self._row_to_stored_run(row)

    def get_run(self, run_id: str) -> StoredRun | None:
        with self._db as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return None if row is None else self._row_to_stored_run(row)

    def list_runs(self, limit: int = 20) -> tuple[StoredRun, ...]:
        with self._db as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return tuple(self._row_to_stored_run(row) for row in rows)

    def reap_orphans(self, scope: CleanupScope) -> int:
        """Detect and seal Runs whose process died while marked RUNNING.

        Tries to ``claim()`` every in-*scope* RUNNING Run. If the claim succeeds
        the owning process is gone — seal the state and mark the Run CANCELLED.
        Returns the count of orphans recovered.
        """
        from milky_frog.harness.state import seal

        count = 0
        for run in self.list_runs(limit=1000):
            if run.status is not RunStatus.RUNNING:
                continue
            if scope.workspace is not None and run.workspace != scope.workspace:
                continue
            try:
                # Hold the claim across seal+save: acquiring it proves no live
                # process owns the Run, and keeping it shut out any process that
                # tries to resume between detection and sealing.
                with self.claim(run.run_id):
                    state = self.load_state(run.run_id)
                    sealed_state, _ = seal(state)
                    self.save_state(
                        run.run_id,
                        sealed_state,
                        status=RunStatus.CANCELLED,
                        final_message="orphaned",
                    )
                    count += 1
            except RunClaimError:
                continue  # Still alive, skip
        return count

    def prune(self, before: datetime, scope: CleanupScope, *, dry_run: bool = False) -> int:
        """Delete stale non-active Runs older than *before* within *scope*."""
        where = "updated_at < ? AND status NOT IN (?, ?, ?)"
        params: list[object] = [
            before.isoformat(),
            RunStatus.RUNNING,
            RunStatus.WAITING_FOR_INPUT,
            RunStatus.WAITING_FOR_APPROVAL,
        ]
        if scope.workspace is not None:
            where += " AND workspace = ?"
            params.append(str(scope.workspace))
        with self._db as conn:
            cursor = conn.execute(f"SELECT COUNT(*) FROM runs WHERE {where}", params)
            count: int = cursor.fetchone()[0]
            if not dry_run and count > 0:
                conn.execute(f"DELETE FROM runs WHERE {where}", params)
            return count

    def resolve_run_id(self, token: str) -> str:
        exact = self.get_run(token)
        if exact is not None:
            return exact.run_id
        matches = tuple(
            run.run_id for run in self.list_runs(limit=100) if run.run_id.startswith(token)
        )
        if not matches:
            raise LookupError(token)
        if len(matches) > 1:
            raise ValueError(token)
        return matches[0]

    @property
    def _db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _initialize(self) -> None:
        with self._db as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    workspace TEXT NOT NULL,
                    status TEXT NOT NULL,
                    state_json TEXT NOT NULL DEFAULT '{}',
                    final_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            columns = {
                str(row["name"]) for row in conn.execute("PRAGMA table_info(runs)").fetchall()
            }
            if "state_json" not in columns:
                conn.execute("ALTER TABLE runs ADD COLUMN state_json TEXT NOT NULL DEFAULT '{}'")
            if "final_message" not in columns:
                conn.execute("ALTER TABLE runs ADD COLUMN final_message TEXT")
            conn.execute("DROP TABLE IF EXISTS run_events")

    @staticmethod
    def _row_to_stored_run(row: sqlite3.Row) -> StoredRun:
        final_message = row["final_message"]
        return StoredRun(
            run_id=str(row["run_id"]),
            workspace=Path(str(row["workspace"])),
            status=RunStatus(str(row["status"])),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            final_message=None if final_message is None else str(final_message),
        )
