"""Backfill, incremental sync, and full-snapshot reconciliation."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from fitness_sync.config import AppConfig, PersonConfig
from fitness_sync.profiles import ProfileMappingError, require_mapped_person, token_path_for
from fitness_sync.storage import (
    CHECKPOINT_LASTUPDATE,
    PersonDatabase,
    WeightRow,
)
from fitness_sync.withings_api import (
    AuthRevokedError,
    TokenSet,
    WithingsClient,
    WeightMeasurement,
    load_tokens,
    save_tokens,
)

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    person_id: str
    ok: bool
    fetched_count: int = 0
    upserted_count: int = 0
    deleted_count: int = 0
    error: str | None = None
    dry_run: bool = False


def _client_from_config(config: AppConfig) -> WithingsClient:
    secrets = config.paths.secrets_dir
    client_id = (secrets / config.client_id_file).read_text(encoding="utf-8").strip()
    client_secret = (
        secrets / config.client_secret_file
    ).read_text(encoding="utf-8").strip()
    return WithingsClient(
        client_id=client_id,
        client_secret=client_secret,
        weight_min_kg=config.weight_min_kg,
        weight_max_kg=config.weight_max_kg,
    )


def _load_and_refresh(
    config: AppConfig,
    person: PersonConfig,
    client: WithingsClient,
    *,
    dry_run: bool,
) -> TokenSet:
    path = token_path_for(config, person)
    tokens = load_tokens(path)
    if tokens is None:
        raise AuthRevokedError(
            f"no tokens for {person.person_id} at {path.name}; run authorize"
        )
    if tokens.is_expired():
        if dry_run:
            logger.warning(
                "dry-run: access token refresh may still occur for %s",
                person.person_id,
            )
        tokens = client.ensure_access_token(tokens, token_path=path)
    return tokens


def _to_rows(measurements: list[WeightMeasurement]) -> list[WeightRow]:
    now = int(time.time())
    return [
        WeightRow(
            source_grpid=m.grpid,
            measured_at_utc=m.measured_at_utc,
            weight_kg=m.weight_kg,
            source_updated_at_utc=None,
            fetched_at_utc=now,
        )
        for m in measurements
    ]


def _filter_for_person(
    person: PersonConfig,
    measurements: list[WeightMeasurement],
) -> list[WeightMeasurement]:
    """Keep only measurements attributable to the mapped identity when userid present."""
    identity = require_mapped_person(person)
    # identity_id stored as string; compare as int when numeric
    try:
        identity_int = int(identity)
    except ValueError as exc:
        raise ProfileMappingError(
            f"{person.person_id} identity_id must be numeric Withings userid"
        ) from exc

    # If every group omits userid, accept the batch only when it is empty of
    # conflicting ids (single-identity authorization). Never assign by weight.
    has_any_userid = any(m.source_userid is not None for m in measurements)
    filtered: list[WeightMeasurement] = []
    for m in measurements:
        if m.source_userid is None:
            if not has_any_userid:
                filtered.append(m)
            # else: mixed/partial attribution — skip unattributed rows
        elif int(m.source_userid) == identity_int:
            filtered.append(m)
        # else: other identity — skip, never reassign by weight
    return filtered


def backfill_person(
    config: AppConfig,
    person: PersonConfig,
    *,
    dry_run: bool = False,
    startdate: int | None = None,
    enddate: int | None = None,
    client: WithingsClient | None = None,
) -> SyncResult:
    client = client or _client_from_config(config)
    db_path = config.paths.person_db(person.person_id, person.database)
    result = SyncResult(person_id=person.person_id, ok=False, dry_run=dry_run)

    try:
        require_mapped_person(person)
        tokens = _load_and_refresh(config, person, client, dry_run=dry_run)
        measurements = client.iter_weight_measurements(
            tokens.access_token,
            startdate=startdate,
            enddate=enddate,
        )
        measurements = _filter_for_person(person, measurements)
        result.fetched_count = len(measurements)

        if dry_run:
            result.ok = True
            logger.info(
                "dry-run backfill person=%s fetched=%s (no DB writes)",
                person.person_id,
                result.fetched_count,
            )
            return result

        with PersonDatabase(db_path) as db:
            run_id = db.start_run()
            try:
                rows = _to_rows(measurements)
                # Idempotent upserts only — never delete on backfill/incomplete pages
                upserted = db.upsert_weights(rows)
                if client.last_updatetime is not None:
                    db.set_state(CHECKPOINT_LASTUPDATE, str(client.last_updatetime))
                db.finish_run(run_id, status="success", fetched_count=upserted)
                result.upserted_count = upserted
                result.ok = True
                logger.info(
                    "backfill person=%s fetched=%s upserted=%s",
                    person.person_id,
                    result.fetched_count,
                    upserted,
                )
            except Exception as exc:
                db.finish_run(
                    run_id, status="error", error_summary=str(exc)[:500]
                )
                raise
    except Exception as exc:
        result.error = str(exc)
        logger.error("backfill failed person=%s error=%s", person.person_id, exc)
    return result


def sync_person(
    config: AppConfig,
    person: PersonConfig,
    *,
    dry_run: bool = False,
    full: bool = False,
    client: WithingsClient | None = None,
) -> SyncResult:
    """Incremental lastupdate sync, or full snapshot reconcile when full=True."""
    if full:
        return full_reconcile_person(
            config, person, dry_run=dry_run, client=client
        )

    client = client or _client_from_config(config)
    db_path = config.paths.person_db(person.person_id, person.database)
    result = SyncResult(person_id=person.person_id, ok=False, dry_run=dry_run)

    try:
        require_mapped_person(person)
        tokens = _load_and_refresh(config, person, client, dry_run=dry_run)

        checkpoint: int | None = None
        if not dry_run:
            with PersonDatabase(db_path) as db:
                raw = db.get_state(CHECKPOINT_LASTUPDATE)
                if raw is not None:
                    checkpoint = max(
                        0, int(raw) - config.lastupdate_overlap_seconds
                    )

        measurements = client.iter_weight_measurements(
            tokens.access_token,
            lastupdate=checkpoint,
        )
        measurements = _filter_for_person(person, measurements)
        result.fetched_count = len(measurements)
        new_checkpoint = client.last_updatetime

        if dry_run:
            result.ok = True
            logger.info(
                "dry-run sync person=%s fetched=%s checkpoint_unchanged",
                person.person_id,
                result.fetched_count,
            )
            return result

        with PersonDatabase(db_path) as db:
            run_id = db.start_run()
            prior_checkpoint = db.get_state(CHECKPOINT_LASTUPDATE)
            try:
                upserted = db.upsert_weights(_to_rows(measurements))
                if new_checkpoint is not None:
                    db.set_state(CHECKPOINT_LASTUPDATE, str(new_checkpoint))
                db.finish_run(run_id, status="success", fetched_count=upserted)
                result.upserted_count = upserted
                result.ok = True
                logger.info(
                    "sync person=%s fetched=%s upserted=%s elapsed_ok=1",
                    person.person_id,
                    result.fetched_count,
                    upserted,
                )
            except Exception as exc:
                # Do not advance checkpoint on failure — rollback leaves prior state
                # but set_state may have run; re-assert prior if needed
                if prior_checkpoint is not None:
                    db.set_state(CHECKPOINT_LASTUPDATE, prior_checkpoint)
                elif new_checkpoint is not None:
                    # clear partial? keep prior None
                    pass
                db.finish_run(
                    run_id, status="error", error_summary=str(exc)[:500]
                )
                raise
    except Exception as exc:
        result.error = str(exc)
        logger.error("sync failed person=%s error=%s", person.person_id, exc)
    return result


def full_reconcile_person(
    config: AppConfig,
    person: PersonConfig,
    *,
    dry_run: bool = False,
    client: WithingsClient | None = None,
    allow_deletes: bool = True,
) -> SyncResult:
    """Full-history snapshot: upsert all, delete stale only if snapshot complete."""
    client = client or _client_from_config(config)
    db_path = config.paths.person_db(person.person_id, person.database)
    result = SyncResult(person_id=person.person_id, ok=False, dry_run=dry_run)

    try:
        require_mapped_person(person)
        tokens = _load_and_refresh(config, person, client, dry_run=dry_run)
        measurements = client.iter_weight_measurements(tokens.access_token)
        # Snapshot is complete only if pagination finished without error
        snapshot_complete = True
        measurements = _filter_for_person(person, measurements)
        result.fetched_count = len(measurements)
        remote_ids = {m.grpid for m in measurements}

        if dry_run:
            result.ok = True
            logger.info(
                "dry-run full reconcile person=%s fetched=%s",
                person.person_id,
                result.fetched_count,
            )
            return result

        with PersonDatabase(db_path) as db:
            run_id = db.start_run()
            prior_checkpoint = db.get_state(CHECKPOINT_LASTUPDATE)
            try:
                upserted = db.upsert_weights(_to_rows(measurements))
                deleted = 0
                if allow_deletes and snapshot_complete:
                    local_ids = db.list_grpids()
                    stale = local_ids - remote_ids
                    deleted = db.delete_grpids(stale)
                if client.last_updatetime is not None:
                    db.set_state(CHECKPOINT_LASTUPDATE, str(client.last_updatetime))
                db.finish_run(run_id, status="success", fetched_count=upserted)
                result.upserted_count = upserted
                result.deleted_count = deleted
                result.ok = True
                logger.info(
                    "full reconcile person=%s fetched=%s upserted=%s deleted=%s",
                    person.person_id,
                    result.fetched_count,
                    upserted,
                    deleted,
                )
            except Exception as exc:
                if prior_checkpoint is not None:
                    db.set_state(CHECKPOINT_LASTUPDATE, prior_checkpoint)
                db.finish_run(
                    run_id, status="error", error_summary=str(exc)[:500]
                )
                raise
    except Exception as exc:
        result.error = str(exc)
        logger.error(
            "full reconcile failed person=%s error=%s", person.person_id, exc
        )
    return result


def sync_all(
    config: AppConfig,
    *,
    dry_run: bool = False,
    full: bool = False,
    client: WithingsClient | None = None,
) -> list[SyncResult]:
    """Sync each enabled person; isolate failures per identity."""
    results: list[SyncResult] = []
    enabled = config.enabled_persons()
    if len(enabled) >= 2 and not config.dual_person_sync_enabled:
        # Do not silently succeed with empty second DB
        raise ProfileMappingError(
            "two or more persons are enabled but dual_person_sync_enabled is false. "
            "Run discovery and only enable dual sync after reliable attribution. "
            "Or disable all but one person in config."
        )
    for person in enabled:
        started = time.monotonic()
        result = sync_person(
            config, person, dry_run=dry_run, full=full, client=client
        )
        elapsed = time.monotonic() - started
        logger.info(
            "person=%s ok=%s fetched=%s elapsed=%.2fs error=%s",
            person.person_id,
            result.ok,
            result.fetched_count,
            elapsed,
            result.error,
        )
        results.append(result)
    return results
