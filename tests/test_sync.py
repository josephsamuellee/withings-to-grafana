"""Sync/backfill orchestration tests with mocked client."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fitness_sync.config import load_config
from fitness_sync.storage import PersonDatabase, WeightRow
from fitness_sync.sync import backfill_person, full_reconcile_person, sync_all, sync_person
from fitness_sync.withings_api import (
    HttpTransport,
    WeightMeasurement,
    WithingsAPIError,
    WithingsClient,
)


class SeqClient(WithingsClient):
    """Client that returns scripted measurement lists / failures."""

    def __init__(self, pages: list[list[WeightMeasurement] | Exception]) -> None:
        super().__init__(client_id="c", client_secret="s")
        self._pages = list(pages)
        self._last_updatetime = 5000

    def ensure_access_token(self, tokens, *, token_path=None):  # type: ignore[no-untyped-def]
        return tokens

    def iter_weight_measurements(self, access_token, **kwargs):  # type: ignore[no-untyped-def]
        if not self._pages:
            return []
        item = self._pages.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def last_updatetime(self) -> int | None:
        return self._last_updatetime


def test_backfill_idempotent_and_interrupt_safe(config_path: Path) -> None:
    config = load_config(config_path, require_mount=False)
    person = config.person("person1")
    meas = [
        WeightMeasurement(1, 1700000000, 70.0, 111),
        WeightMeasurement(2, 1700000000, 70.5, 111),
    ]
    client = SeqClient([meas])
    r1 = backfill_person(config, person, client=client)
    assert r1.ok and r1.upserted_count == 2

    # Simulate interrupt mid-way then re-run
    client2 = SeqClient([meas])
    r2 = backfill_person(config, person, client=client2)
    assert r2.ok
    with PersonDatabase(config.paths.person_db("person1", person.database)) as db:
        assert db.count_weights() == 2


def test_incremental_checkpoint_not_advanced_on_failure(config_path: Path) -> None:
    config = load_config(config_path, require_mount=False)
    person = config.person("person1")
    db_path = config.paths.person_db("person1", person.database)
    with PersonDatabase(db_path) as db:
        db.set_state("lastupdate", "1000")
        db.upsert_weights([WeightRow(9, 100, 70.0)])

    client = SeqClient([WithingsAPIError("boom mid pagination")])
    result = sync_person(config, person, client=client)
    assert not result.ok
    with PersonDatabase(db_path) as db:
        assert db.get_state("lastupdate") == "1000"
        assert db.count_weights() == 1


def test_dry_run_skips_db_writes(config_path: Path) -> None:
    config = load_config(config_path, require_mount=False)
    person = config.person("person1")
    db_path = config.paths.person_db("person1", person.database)
    client = SeqClient(
        [[WeightMeasurement(1, 1700000000, 70.0, 111)]]
    )
    result = sync_person(config, person, dry_run=True, client=client)
    assert result.ok and result.dry_run
    assert not db_path.exists()


def test_full_reconcile_updates_and_deletes(config_path: Path) -> None:
    config = load_config(config_path, require_mount=False)
    person = config.person("person1")
    db_path = config.paths.person_db("person1", person.database)
    with PersonDatabase(db_path) as db:
        db.upsert_weights(
            [
                WeightRow(1, 100, 70.0),
                WeightRow(2, 200, 71.0),  # will be stale
            ]
        )

    remote = [
        WeightMeasurement(1, 100, 69.5, 111),  # corrected
        WeightMeasurement(3, 300, 72.0, 111),
    ]
    client = SeqClient([remote])
    result = full_reconcile_person(config, person, client=client)
    assert result.ok
    with PersonDatabase(db_path) as db:
        assert db.count_weights() == 2
        assert 2 not in db.list_grpids()
        rows = db._conn.execute(
            "SELECT weight_kg FROM weight_measurements WHERE source_grpid=1"
        ).fetchone()
        assert rows[0] == 69.5


def test_full_reconcile_no_delete_on_incomplete(config_path: Path) -> None:
    config = load_config(config_path, require_mount=False)
    person = config.person("person1")
    db_path = config.paths.person_db("person1", person.database)
    with PersonDatabase(db_path) as db:
        db.upsert_weights([WeightRow(1, 100, 70.0), WeightRow(2, 200, 71.0)])

    client = SeqClient([WithingsAPIError("incomplete")])
    result = full_reconcile_person(config, person, client=client)
    assert not result.ok
    with PersonDatabase(db_path) as db:
        assert db.count_weights() == 2


def test_sync_all_isolates_failures(config_path: Path, app_root: Path) -> None:
    # Enable person2 with tokens
    cfg = (app_root / "config" / "config.toml").read_text(encoding="utf-8")
    cfg = cfg.replace("dual_person_sync_enabled = false", "dual_person_sync_enabled = true")
    cfg = cfg.replace(
        'token_file = "tokens_identity2.json"\nenabled = false',
        'token_file = "tokens_identity2.json"\nenabled = true',
    )
    (app_root / "config" / "config.toml").write_text(cfg, encoding="utf-8")
    tokens = {
        "access_token": "a2",
        "refresh_token": "r2",
        "expires_at": 9999999999,
        "userid": 222,
    }
    (app_root / "secrets" / "tokens_identity2.json").write_text(
        json.dumps(tokens), encoding="utf-8"
    )

    config = load_config(config_path, require_mount=False)

    class DualClient(WithingsClient):
        def __init__(self) -> None:
            super().__init__(client_id="c", client_secret="s")
            self.calls = 0
            self._last_updatetime = 9

        def ensure_access_token(self, tokens, *, token_path=None):  # type: ignore[no-untyped-def]
            return tokens

        def iter_weight_measurements(self, access_token, **kwargs):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.calls == 1:
                return [WeightMeasurement(1, 100, 70.0, 111)]
            raise WithingsAPIError("person2 boom")

        @property
        def last_updatetime(self) -> int | None:
            return self._last_updatetime

    results = sync_all(config, client=DualClient())
    assert results[0].ok
    assert not results[1].ok
    p1 = config.paths.person_db("person1", "person1.sqlite3")
    with PersonDatabase(p1) as db:
        assert db.count_weights() == 1
        assert db.get_state("lastupdate") == "9"
