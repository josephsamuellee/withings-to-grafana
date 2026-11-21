"""Backup/restore dry-lab verification."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fitness_sync.storage import PersonDatabase, WeightRow


def test_restore_from_backup_artifact(tmp_path: Path) -> None:
    src = tmp_path / "data" / "person1.sqlite3"
    bak = tmp_path / "backups" / "person1-lab.sqlite3"
    lab = tmp_path / "fitness-restore-lab" / "person1.sqlite3"

    with PersonDatabase(src) as db:
        db.upsert_weights(
            [
                WeightRow(1, 1700000000, 70.1),
                WeightRow(2, 1700001000, 70.4),
            ]
        )
        db.backup_to(bak)

    lab.parent.mkdir(parents=True)
    lab.write_bytes(bak.read_bytes())

    conn = sqlite3.connect(lab)
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "weight_measurements" in tables
        count = conn.execute(
            "SELECT COUNT(*) FROM weight_measurements"
        ).fetchone()[0]
        assert count == 2
    finally:
        conn.close()
