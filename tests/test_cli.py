"""CLI help, status, doctor, and logging tests."""

from __future__ import annotations

import logging
from pathlib import Path

from fitness_sync.cli import main


def test_help_exits_zero() -> None:
    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0


def test_status_no_secrets(config_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    code = main(
        ["--config", str(config_path), "--skip-mount-check", "status"]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "person1" in out
    assert "test-client-secret" not in out
    assert "access-1" not in out


def test_doctor_ok(config_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    code = main(
        ["--config", str(config_path), "--skip-mount-check", "doctor"]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "[OK]" in out
    assert "test-client-secret" not in out


def test_doctor_missing_mount(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    missing = tmp_path / "nope" / "config.toml"
    code = main(["--config", str(missing), "doctor"])
    assert code != 0


def test_doctor_missing_tokens(config_path: Path, app_root: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    (app_root / "secrets" / "tokens_identity1.json").unlink()
    code = main(
        ["--config", str(config_path), "--skip-mount-check", "doctor"]
    )
    assert code != 0
    out = capsys.readouterr().out
    assert "person1_token_present" in out
    assert "[FAIL]" in out


def test_sync_logging_no_weights(
    config_path: Path, caplog, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from fitness_sync import sync as sync_mod
    from fitness_sync.withings_api import WeightMeasurement
    from fitness_sync.sync import SyncResult

    def fake_sync_person(config, person, **kwargs):  # type: ignore[no-untyped-def]
        logging.getLogger("fitness_sync.sync").info(
            "sync person=%s fetched=%s upserted=%s",
            person.person_id,
            1,
            0,
        )
        return SyncResult(
            person_id=person.person_id, ok=True, fetched_count=1, dry_run=True
        )

    monkeypatch.setattr(sync_mod, "sync_person", fake_sync_person)
    # Patch through cli import path
    import fitness_sync.cli as cli_mod

    monkeypatch.setattr(cli_mod, "sync_person", fake_sync_person)

    caplog.set_level(logging.INFO)
    code = main(
        [
            "--config",
            str(config_path),
            "--skip-mount-check",
            "sync",
            "--person",
            "person1",
            "--dry-run",
        ]
    )
    assert code == 0
    assert "70.0" not in caplog.text
    assert "access-1" not in caplog.text
    _ = WeightMeasurement  # silence unused in older linters
