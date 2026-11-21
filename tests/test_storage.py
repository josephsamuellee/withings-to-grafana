"""SQLite storage tests."""

from __future__ import annotations

from pathlib import Path

from fitness_sync.storage import PersonDatabase, WeightRow


def test_schema_per_person(tmp_path: Path) -> None:
    db1 = PersonDatabase(tmp_path / "person1.sqlite3")
    db2 = PersonDatabase(tmp_path / "person2.sqlite3")
    assert db1.path != db2.path
    assert db1.count_weights() == 0
    assert db2.count_weights() == 0
    db1.close()
    db2.close()


def test_idempotent_upsert_and_distinct_timestamps(tmp_path: Path) -> None:
    with PersonDatabase(tmp_path / "p.sqlite3") as db:
        ts = 1_700_000_000
        rows = [
            WeightRow(source_grpid=1, measured_at_utc=ts, weight_kg=70.5),
            WeightRow(source_grpid=2, measured_at_utc=ts, weight_kg=71.0),
        ]
        db.upsert_weights(rows)
        db.upsert_weights(rows)  # idempotent
        assert db.count_weights() == 2
        db.upsert_weights(
            [WeightRow(source_grpid=1, measured_at_utc=ts, weight_kg=70.0)]
        )
        # still one row for grpid 1, updated
        assert db.count_weights() == 2


def test_checkpoint_and_runs(tmp_path: Path) -> None:
    with PersonDatabase(tmp_path / "p.sqlite3") as db:
        run_id = db.start_run()
        db.set_state("lastupdate", "100")
        db.finish_run(run_id, status="success", fetched_count=3)
        assert db.get_state("lastupdate") == "100"
        latest = db.latest_run()
        assert latest is not None
        assert latest["status"] == "success"
        assert latest["fetched_count"] == 3


def test_backup_row_counts(tmp_path: Path) -> None:
    src = tmp_path / "p.sqlite3"
    dest = tmp_path / "backups" / "p-bak.sqlite3"
    with PersonDatabase(src) as db:
        db.upsert_weights(
            [
                WeightRow(1, 100, 70.0),
                WeightRow(2, 200, 71.0),
            ]
        )
        db.backup_to(dest)
    with PersonDatabase(dest) as bak:
        assert bak.count_weights() == 2
