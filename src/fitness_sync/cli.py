"""CLI: authorize, sync, backfill, status, doctor, discover."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from fitness_sync.config import AppConfig, load_config
from fitness_sync.lock import ProcessLock, ProcessLockError
from fitness_sync.paths import AppRootError, ensure_app_root_usable
from fitness_sync.profiles import (
    DUAL_PROFILE_BLOCKED_MESSAGE,
    ProfileMappingError,
    discover_identities,
    require_mapped_person,
    token_path_for,
)
from fitness_sync.storage import PersonDatabase
from fitness_sync.sync import backfill_person, sync_all, sync_person
from fitness_sync.withings_api import (
    AuthRevokedError,
    OAuthCallbackServer,
    WithingsAPIError,
    WithingsClient,
    read_secret_file,
    save_tokens,
)


EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CONFIG = 2
EXIT_AUTH = 3
EXIT_LOCK = 4


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )


def _default_config_path() -> Path:
    env = os.environ.get("FITNESS_SYNC_CONFIG")
    if env:
        return Path(env)
    # Prefer config next to CWD when developing; production uses absolute path via env
    candidate = Path.cwd() / "config" / "config.toml"
    return candidate


def _load(args: argparse.Namespace) -> AppConfig:
    path = Path(args.config)
    return load_config(
        path,
        require_mount=not args.skip_mount_check,
        create_subdirs=False,
    )


def cmd_authorize(args: argparse.Namespace) -> int:
    config = _load(args)
    secrets = config.paths.secrets_dir
    client_id = read_secret_file(secrets / config.client_id_file)
    client_secret = read_secret_file(secrets / config.client_secret_file)
    client = WithingsClient(client_id=client_id, client_secret=client_secret)

    redirect_uri = (
        f"http://{config.callback_host}:{config.callback_port}{config.callback_path}"
    )
    server = OAuthCallbackServer(
        config.callback_host, config.callback_port, config.callback_path
    )
    server.start()
    url = client.build_authorize_url(redirect_uri=redirect_uri)
    print("Open this URL in a browser to authorize:")
    print(url)
    print(
        f"Listening on {redirect_uri} (temporary; will stop after success/failure)."
    )
    print(
        "If authorizing from another machine, use SSH port forwarding, e.g.:\n"
        f"  ssh -L {config.callback_port}:127.0.0.1:{config.callback_port} pi@<raspberry-pi>"
    )
    try:
        code = server.wait(timeout=args.timeout)
        # Code is valid ~30s — exchange immediately; never log it
        tokens = client.exchange_code(code, redirect_uri=redirect_uri)
    except WithingsAPIError as exc:
        print(f"Authorization failed: {exc}", file=sys.stderr)
        return EXIT_AUTH
    finally:
        server.shutdown()

    person = config.person(args.person)
    path = token_path_for(config, person)
    save_tokens(path, tokens)
    print(f"Tokens saved for {person.person_id} ({path.name}).")
    if tokens.userid is not None:
        print(
            f"Withings userid={tokens.userid}. "
            f"Set persons.{person.person_id}.identity_id = \"{tokens.userid}\" in config.toml."
        )
    return EXIT_OK


def cmd_backfill(args: argparse.Namespace) -> int:
    config = _load(args)
    lock = ProcessLock(config.paths.lock_file)
    try:
        lock.acquire()
    except ProcessLockError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_LOCK
    try:
        if args.dry_run:
            print(
                "dry-run: will fetch/validate but not modify SQLite or checkpoints; "
                "token refresh may still occur if the access token is expired."
            )
        person = config.person(args.person)
        result = backfill_person(config, person, dry_run=args.dry_run)
        if not result.ok:
            print(f"backfill failed: {result.error}", file=sys.stderr)
            return EXIT_ERROR
        print(
            f"backfill ok person={result.person_id} fetched={result.fetched_count} "
            f"upserted={result.upserted_count} dry_run={result.dry_run}"
        )
        return EXIT_OK
    finally:
        lock.release()


def cmd_sync(args: argparse.Namespace) -> int:
    config = _load(args)
    lock = ProcessLock(config.paths.lock_file)
    try:
        lock.acquire()
    except ProcessLockError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_LOCK
    try:
        if args.dry_run:
            print(
                "dry-run: will fetch/validate but not modify SQLite or checkpoints; "
                "token refresh may still occur if the access token is expired."
            )
        if args.person:
            person = config.person(args.person)
            results = [
                sync_person(
                    config, person, dry_run=args.dry_run, full=args.full
                )
            ]
        else:
            try:
                results = sync_all(
                    config, dry_run=args.dry_run, full=args.full
                )
            except ProfileMappingError as exc:
                print(str(exc), file=sys.stderr)
                return EXIT_CONFIG

        failed = [r for r in results if not r.ok]
        for r in results:
            status = "ok" if r.ok else "FAIL"
            print(
                f"sync {status} person={r.person_id} fetched={r.fetched_count} "
                f"upserted={r.upserted_count} deleted={r.deleted_count} "
                f"dry_run={r.dry_run}"
                + (f" error={r.error}" if r.error else "")
            )
        return EXIT_ERROR if failed else EXIT_OK
    except AuthRevokedError as exc:
        print(f"reauthorization required: {exc}", file=sys.stderr)
        return EXIT_AUTH
    finally:
        lock.release()


def cmd_status(args: argparse.Namespace) -> int:
    config = _load(args)
    for person in config.persons:
        db_path = config.paths.person_db(person.person_id, person.database)
        token_path = token_path_for(config, person)
        print(f"[{person.person_id}] enabled={person.enabled}")
        print(f"  database={db_path} exists={db_path.exists()}")
        print(
            f"  identity_id={person.identity_id or '(unset)'} "
            f"token_file={token_path.name} present={token_path.exists()}"
        )
        if db_path.exists():
            with PersonDatabase(db_path) as db:
                print(f"  weight_rows={db.count_weights()}")
                print(f"  lastupdate={db.get_state('lastupdate')}")
                run = db.latest_run()
                if run:
                    print(
                        f"  latest_run status={run['status']} "
                        f"fetched={run['fetched_count']}"
                    )
    return EXIT_OK


def cmd_discover(args: argparse.Namespace) -> int:
    config = _load(args)
    person = config.person(args.person)
    secrets = config.paths.secrets_dir
    client = WithingsClient(
        client_id=read_secret_file(secrets / config.client_id_file),
        client_secret=read_secret_file(secrets / config.client_secret_file),
    )
    from fitness_sync.withings_api import load_tokens

    tokens = load_tokens(token_path_for(config, person))
    if tokens is None:
        print("No tokens; run authorize first.", file=sys.stderr)
        return EXIT_AUTH
    result = discover_identities(client, tokens)
    print(result.detail)
    print(f"identities_found={result.identities_found}")
    print(f"dual_attributable={result.dual_attributable}")
    if not result.dual_attributable:
        return EXIT_CONFIG
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    failures = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        mark = "OK" if ok else "FAIL"
        if not ok:
            failures += 1
        print(f"[{mark}] {name}" + (f": {detail}" if detail else ""))

    config_path = Path(args.config)
    check("config_file", config_path.exists(), str(config_path))
    if not config_path.exists():
        return EXIT_CONFIG

    try:
        config = load_config(
            config_path,
            require_mount=not args.skip_mount_check,
            create_subdirs=False,
        )
        check("config_load", True)
        check("app_root", True, str(config.paths.root))
    except (AppRootError, ValueError, OSError) as exc:
        check("app_root", False, str(exc))
        return EXIT_CONFIG

    root = config.paths.root
    check("root_writable", os.access(root, os.W_OK))
    check("secrets_dir", config.paths.secrets_dir.is_dir())
    check("data_dir", config.paths.data_dir.is_dir())

    # Clock sanity: not wildly wrong
    now = time.time()
    check("clock", now > 1_600_000_000, f"epoch={int(now)}")

    for person in config.persons:
        token_path = token_path_for(config, person)
        check(
            f"{person.person_id}_token_present",
            token_path.exists() if person.enabled else True,
            token_path.name if person.enabled else "disabled",
        )
        mapped = bool(person.identity_id)
        check(
            f"{person.person_id}_identity_mapped",
            mapped if person.enabled else True,
            person.identity_id or "(unset)",
        )
        db_path = config.paths.person_db(person.person_id, person.database)
        if db_path.exists():
            try:
                with PersonDatabase(db_path) as db:
                    db.count_weights()
                check(f"{person.person_id}_schema", True)
            except Exception as exc:
                check(f"{person.person_id}_schema", False, str(exc))
        elif person.enabled:
            check(f"{person.person_id}_schema", True, "db not created yet")

    enabled = config.enabled_persons()
    if len(enabled) >= 2:
        check(
            "dual_person_gate",
            config.dual_person_sync_enabled,
            "dual_person_sync_enabled must be true after discovery"
            if not config.dual_person_sync_enabled
            else "enabled",
        )
        if not config.dual_person_sync_enabled:
            print(DUAL_PROFILE_BLOCKED_MESSAGE)

    # Never print secret contents
    check(
        "client_id_present",
        (config.paths.secrets_dir / config.client_id_file).exists(),
    )
    check(
        "client_secret_present",
        (config.paths.secrets_dir / config.client_secret_file).exists(),
    )

    return EXIT_OK if failures == 0 else EXIT_ERROR


def cmd_backup(args: argparse.Namespace) -> int:
    config = _load(args)
    person = config.person(args.person)
    db_path = config.paths.person_db(person.person_id, person.database)
    if not db_path.exists():
        print(f"database missing: {db_path}", file=sys.stderr)
        return EXIT_ERROR
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dest = config.paths.backups_dir / f"{person.person_id}-{ts}.sqlite3"
    with PersonDatabase(db_path) as db:
        db.backup_to(dest)
    print(f"backup written: {dest}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fitness-sync",
        description="Self-hosted Withings weight synchronizer",
    )
    parser.add_argument(
        "--config",
        default=str(_default_config_path()),
        help="Absolute path to config.toml",
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--skip-mount-check",
        action="store_true",
        help="Skip mount probing (tests/dev only)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_auth = sub.add_parser("authorize", help="OAuth authorization-code bootstrap")
    p_auth.add_argument("--person", default="person1")
    p_auth.add_argument("--timeout", type=float, default=300.0)
    p_auth.set_defaults(func=cmd_authorize)

    p_bf = sub.add_parser("backfill", help="Full-history import for one person")
    p_bf.add_argument("--person", required=True)
    p_bf.add_argument("--dry-run", action="store_true")
    p_bf.set_defaults(func=cmd_backfill)

    p_sync = sub.add_parser("sync", help="Incremental or full sync")
    p_sync.add_argument("--person", default=None, help="Single person; default --all")
    p_sync.add_argument("--all", action="store_true", help="Sync all enabled persons")
    p_sync.add_argument("--full", action="store_true", help="Full-history reconcile")
    p_sync.add_argument("--dry-run", action="store_true")
    p_sync.set_defaults(func=cmd_sync)

    p_status = sub.add_parser("status", help="Show sync status without secrets")
    p_status.set_defaults(func=cmd_status)

    p_doc = sub.add_parser("doctor", help="Validate mount, config, tokens, mappings")
    p_doc.set_defaults(func=cmd_doctor)

    p_disc = sub.add_parser(
        "discover",
        help="Read-only dual-profile attribution discovery",
    )
    p_disc.add_argument("--person", default="person1")
    p_disc.set_defaults(func=cmd_discover)

    p_bak = sub.add_parser("backup", help="Consistent SQLite backup for one person")
    p_bak.add_argument("--person", required=True)
    p_bak.set_defaults(func=cmd_backup)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    try:
        return int(args.func(args))
    except AppRootError as exc:
        logging.error("%s", exc)
        return EXIT_CONFIG
    except AuthRevokedError as exc:
        logging.error("reauthorization required: %s", exc)
        return EXIT_AUTH
    except ProfileMappingError as exc:
        logging.error("%s", exc)
        return EXIT_CONFIG
    except ProcessLockError as exc:
        logging.error("%s", exc)
        return EXIT_LOCK


if __name__ == "__main__":
    sys.exit(main())
