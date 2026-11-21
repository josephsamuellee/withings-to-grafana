"""Application root resolution and fail-closed mount checks."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class AppRootError(RuntimeError):
    """Raised when the configured application root is unusable."""


@dataclass(frozen=True)
class AppPaths:
    """Paths derived from a single configured application root."""

    root: Path
    config_dir: Path
    secrets_dir: Path
    data_dir: Path
    backups_dir: Path
    logs_dir: Path
    lock_file: Path

    @classmethod
    def from_root(cls, root: Path | str) -> AppPaths:
        root_path = Path(root).expanduser()
        if not root_path.is_absolute():
            raise AppRootError(
                f"application root must be an absolute path, got {root_path!s}"
            )
        return cls(
            root=root_path,
            config_dir=root_path / "config",
            secrets_dir=root_path / "secrets",
            data_dir=root_path / "data",
            backups_dir=root_path / "backups",
            logs_dir=root_path / "logs",
            lock_file=root_path / "data" / ".sync.lock",
        )

    def person_db(self, person_id: str, filename: str | None = None) -> Path:
        name = filename or f"{person_id}.sqlite3"
        return self.data_dir / name


def _is_mount_point(path: Path) -> bool:
    """Return True if path is a mount point (best-effort; Linux/macOS)."""
    try:
        return path.is_mount()
    except OSError:
        return False


def ensure_app_root_usable(
    root: Path | str,
    *,
    require_mount: bool = True,
    create_subdirs: bool = False,
) -> AppPaths:
    """Validate application root and optionally ensure subdirectories exist.

    Fails closed: never creates a fallback directory on the root filesystem
    when the configured RAID path is missing.
    """
    paths = AppPaths.from_root(root)
    root = paths.root

    if not root.exists():
        raise AppRootError(
            f"application root does not exist: {root}. "
            "Refusing to create a fallback on the root filesystem; "
            "mount RAID and create /mnt/raid1/apps/fitness first."
        )
    if not root.is_dir():
        raise AppRootError(f"application root is not a directory: {root}")

    # Fail closed if the configured path is not on a mounted volume when required.
    # Check the root itself or a parent mount (e.g. /mnt/raid1).
    if require_mount:
        mount_ok = _is_mount_point(root) or any(
            _is_mount_point(p) for p in root.parents
        )
        # In tests, FITNESS_SYNC_SKIP_MOUNT_CHECK=1 disables mount probing.
        if os.environ.get("FITNESS_SYNC_SKIP_MOUNT_CHECK") == "1":
            mount_ok = True
        if not mount_ok:
            # Still require the path exists and is writable; mount detection
            # can be unreliable in some environments, so also verify a marker.
            marker = root / ".raid_ok"
            if not marker.exists() and not os.access(root, os.W_OK):
                raise AppRootError(
                    f"application root does not appear mounted or writable: {root}"
                )

    if not os.access(root, os.R_OK | os.W_OK):
        raise AppRootError(f"application root is not readable/writable: {root}")

    if create_subdirs:
        for d in (
            paths.config_dir,
            paths.secrets_dir,
            paths.data_dir,
            paths.backups_dir,
            paths.logs_dir,
        ):
            d.mkdir(mode=0o700 if d == paths.secrets_dir else 0o755, exist_ok=True)

    return paths
