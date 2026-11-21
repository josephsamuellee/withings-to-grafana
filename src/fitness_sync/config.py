"""Load non-secret configuration from TOML."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fitness_sync.paths import AppPaths, ensure_app_root_usable


@dataclass(frozen=True)
class PersonConfig:
    person_id: str
    database: str
    identity_id: str | None = None
    token_file: str | None = None
    enabled: bool = True


@dataclass(frozen=True)
class AppConfig:
    paths: AppPaths
    timezone: str = "America/Los_Angeles"
    callback_host: str = "127.0.0.1"
    callback_port: int = 8765
    callback_path: str = "/callback"
    oauth_scope: str = "user.metrics"
    client_id_file: str = "client_id"
    client_secret_file: str = "client_secret"
    lastupdate_overlap_seconds: int = 300
    http_timeout_seconds: float = 30.0
    http_max_retries: int = 5
    weight_min_kg: float = 20.0
    weight_max_kg: float = 400.0
    persons: tuple[PersonConfig, ...] = field(default_factory=tuple)
    dual_person_sync_enabled: bool = False

    def person(self, person_id: str) -> PersonConfig:
        for p in self.persons:
            if p.person_id == person_id:
                return p
        raise KeyError(f"unknown person id: {person_id}")

    def enabled_persons(self) -> list[PersonConfig]:
        return [p for p in self.persons if p.enabled]


def _parse_persons(raw: dict[str, Any]) -> tuple[PersonConfig, ...]:
    persons_raw = raw.get("persons") or {}
    result: list[PersonConfig] = []
    for person_id, cfg in persons_raw.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"persons.{person_id} must be a table")
        result.append(
            PersonConfig(
                person_id=person_id,
                database=str(cfg.get("database", f"{person_id}.sqlite3")),
                identity_id=cfg.get("identity_id"),
                token_file=cfg.get("token_file"),
                enabled=bool(cfg.get("enabled", True)),
            )
        )
    if not result:
        # Default skeleton for person1 / person2
        result = [
            PersonConfig(person_id="person1", database="person1.sqlite3"),
            PersonConfig(person_id="person2", database="person2.sqlite3", enabled=False),
        ]
    return tuple(result)


def load_config(
    config_path: Path | str,
    *,
    require_mount: bool = True,
    create_subdirs: bool = False,
) -> AppConfig:
    path = Path(config_path)
    if not path.is_absolute():
        raise ValueError(f"config path must be absolute: {path}")
    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    app = raw.get("app") or {}
    root = app.get("root")
    if not root:
        raise ValueError("config [app].root is required")

    paths = ensure_app_root_usable(
        root, require_mount=require_mount, create_subdirs=create_subdirs
    )
    oauth = raw.get("oauth") or {}
    sync = raw.get("sync") or {}
    validation = raw.get("validation") or {}

    return AppConfig(
        paths=paths,
        timezone=str(app.get("timezone", "America/Los_Angeles")),
        callback_host=str(oauth.get("callback_host", "127.0.0.1")),
        callback_port=int(oauth.get("callback_port", 8765)),
        callback_path=str(oauth.get("callback_path", "/callback")),
        oauth_scope=str(oauth.get("scope", "user.metrics")),
        client_id_file=str(oauth.get("client_id_file", "client_id")),
        client_secret_file=str(oauth.get("client_secret_file", "client_secret")),
        lastupdate_overlap_seconds=int(sync.get("lastupdate_overlap_seconds", 300)),
        http_timeout_seconds=float(sync.get("http_timeout_seconds", 30.0)),
        http_max_retries=int(sync.get("http_max_retries", 5)),
        weight_min_kg=float(validation.get("weight_min_kg", 20.0)),
        weight_max_kg=float(validation.get("weight_max_kg", 400.0)),
        persons=_parse_persons(raw),
        dual_person_sync_enabled=bool(sync.get("dual_person_sync_enabled", False)),
    )
