"""Per-person SQLite schema, upserts, checkpoints, and backups."""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS weight_measurements (
    source_grpid INTEGER PRIMARY KEY,
    measured_at_utc INTEGER NOT NULL,
    weight_kg REAL NOT NULL,
    source_updated_at_utc INTEGER,
    fetched_at_utc INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_weight_measured_at
    ON weight_measurements(measured_at_utc);

CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at_utc INTEGER NOT NULL,
    finished_at_utc INTEGER,
    status TEXT NOT NULL,
    fetched_count INTEGER NOT NULL DEFAULT 0,
    error_summary TEXT
);
"""

CHECKPOINT_LASTUPDATE = "lastupdate"
CHECKPOINT_BACKFILL_OFFSET = "backfill_offset"


@dataclass(frozen=True)
class WeightRow:
    source_grpid: int
    measured_at_utc: int
    weight_kg: float
    source_updated_at_utc: int | None = None
    fetched_at_utc: int | None = None


class PersonDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> PersonDatabase:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def upsert_weights(self, rows: list[WeightRow]) -> int:
        if not rows:
            return 0
        now = int(time.time())
        with self.transaction() as conn:
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO weight_measurements (
                        source_grpid, measured_at_utc, weight_kg,
                        source_updated_at_utc, fetched_at_utc
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(source_grpid) DO UPDATE SET
                        measured_at_utc = excluded.measured_at_utc,
                        weight_kg = excluded.weight_kg,
                        source_updated_at_utc = excluded.source_updated_at_utc,
                        fetched_at_utc = excluded.fetched_at_utc
                    """,
                    (
                        row.source_grpid,
                        row.measured_at_utc,
                        row.weight_kg,
                        row.source_updated_at_utc,
                        row.fetched_at_utc or now,
                    ),
                )
        return len(rows)

    def count_weights(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM weight_measurements")
        return int(cur.fetchone()[0])

    def list_grpids(self) -> set[int]:
        cur = self._conn.execute("SELECT source_grpid FROM weight_measurements")
        return {int(r[0]) for r in cur.fetchall()}

    def delete_grpids(self, grpids: set[int]) -> int:
        if not grpids:
            return 0
        with self.transaction() as conn:
            for gid in grpids:
                conn.execute(
                    "DELETE FROM weight_measurements WHERE source_grpid = ?",
                    (gid,),
                )
        return len(grpids)

    def get_state(self, key: str) -> str | None:
        cur = self._conn.execute(
            "SELECT value FROM sync_state WHERE key = ?", (key,)
        )
        row = cur.fetchone()
        return None if row is None else str(row[0])

    def set_state(self, key: str, value: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO sync_state (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def start_run(self) -> int:
        now = int(time.time())
        with self.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO sync_runs (started_at_utc, status, fetched_count)
                VALUES (?, 'running', 0)
                """,
                (now,),
            )
            return int(cur.lastrowid)

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        fetched_count: int = 0,
        error_summary: str | None = None,
    ) -> None:
        now = int(time.time())
        with self.transaction() as conn:
            conn.execute(
                """
                UPDATE sync_runs
                SET finished_at_utc = ?, status = ?, fetched_count = ?,
                    error_summary = ?
                WHERE id = ?
                """,
                (now, status, fetched_count, error_summary, run_id),
            )

    def latest_run(self) -> sqlite3.Row | None:
        cur = self._conn.execute(
            "SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1"
        )
        return cur.fetchone()

    def backup_to(self, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.unlink()
        dest_conn = sqlite3.connect(dest)
        try:
            with dest_conn:
                self._conn.backup(dest_conn)
        finally:
            dest_conn.close()
        return dest
