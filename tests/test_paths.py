"""Tests for application root fail-closed behavior."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from fitness_sync.paths import AppRootError, ensure_app_root_usable


def test_missing_root_fails_closed(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist" / "fitness"
    with pytest.raises(AppRootError, match="does not exist"):
        ensure_app_root_usable(missing, require_mount=False)


def test_relative_root_rejected() -> None:
    with pytest.raises(AppRootError, match="absolute"):
        ensure_app_root_usable("relative/path", require_mount=False)


def test_usable_root(tmp_path: Path) -> None:
    root = tmp_path / "fitness"
    root.mkdir()
    os.environ["FITNESS_SYNC_SKIP_MOUNT_CHECK"] = "1"
    paths = ensure_app_root_usable(root, require_mount=True, create_subdirs=True)
    assert paths.data_dir.is_dir()
    assert paths.secrets_dir.is_dir()
    assert paths.person_db("person1").name == "person1.sqlite3"
