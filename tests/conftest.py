"""Shared fixtures for fitness_sync tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# Disable mount probing in unit tests
os.environ["FITNESS_SYNC_SKIP_MOUNT_CHECK"] = "1"


@pytest.fixture
def app_root(tmp_path: Path) -> Path:
    root = tmp_path / "fitness"
    for name in ("config", "secrets", "data", "backups", "logs"):
        (root / name).mkdir(parents=True)
    (root / ".raid_ok").write_text("ok\n", encoding="utf-8")
    (root / "secrets" / "client_id").write_text("test-client-id\n", encoding="utf-8")
    (root / "secrets" / "client_secret").write_text(
        "test-client-secret\n", encoding="utf-8"
    )
    config = f"""
[app]
root = "{root.as_posix()}"
timezone = "America/Los_Angeles"

[oauth]
callback_host = "127.0.0.1"
callback_port = 8765
callback_path = "/callback"

[sync]
lastupdate_overlap_seconds = 60
dual_person_sync_enabled = false

[validation]
weight_min_kg = 20.0
weight_max_kg = 400.0

[persons.person1]
database = "person1.sqlite3"
identity_id = "111"
token_file = "tokens_identity1.json"
enabled = true

[persons.person2]
database = "person2.sqlite3"
identity_id = "222"
token_file = "tokens_identity2.json"
enabled = false
"""
    (root / "config" / "config.toml").write_text(config, encoding="utf-8")
    tokens = {
        "access_token": "access-1",
        "refresh_token": "refresh-1",
        "expires_at": 9999999999,
        "userid": 111,
    }
    (root / "secrets" / "tokens_identity1.json").write_text(
        json.dumps(tokens), encoding="utf-8"
    )
    return root


@pytest.fixture
def config_path(app_root: Path) -> Path:
    return app_root / "config" / "config.toml"
