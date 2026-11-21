# Design

## Context

See proposal.md for motivation and scope. This repo is greenfield (`README.md` title only; no application package, OpenAPI file, or prior specs). The Withings OpenAPI 3.0.3 / API v2.0 contract (user-supplied `openapi.json`) is the primary endpoint reference; legacy shared-login multi-profile behavior is unverified and gates two-person automation.

Target runtime: Raspberry Pi 4, Linux, application root `/mnt/raid1/apps/fitness`, systemd timer invoking a short-lived process. No web portal, PostgreSQL, Grafana, or always-on poller.

## Goals / Non-Goals

**Goals:**

- Single importable package `fitness_sync` with a thin CLI, clear module boundaries, and one process per scheduled invocation.
- Path resolution from one configured application root; fail closed if RAID is missing.
- OAuth bootstrap + refresh that is safe on a headless Pi (SSH port-forward for localhost callback).
- Idempotent weight sync with explicit profile mapping and mocked-first testability.
- Deploy artifacts (unit/timer) and operator docs that match the RAID layout.

**Non-Goals:**

- Implementing Grafana or any HTTP app server.
- Auto-splitting profiles by weight or migrating Withings accounts.
- Containerizing the Pi or shipping an independent Linux rootfs.
- Promising multi-profile sync before the live discovery PoC succeeds.

## Decisions

### D1 — Fresh package under RAID layout, not extending a prototype

**Choice:** Generate a self-contained `src/fitness_sync/` tree matching the proposed filesystem layout; ignore any historical `/opt` or portal-oriented prototypes.

**Alternatives:** Adapt an existing prototype in-place; install under `/opt` with data on RAID.

**Rationale:** Proposal explicitly forbids `/opt` application paths and asks for a fresh implementation. A clean package boundary keeps relocatable roots and tests simple.

### D2 — Module split within one short-lived process

**Choice:**

| Module | Responsibility |
|--------|----------------|
| `cli.py` | argparse entrypoints: authorize, sync, backfill, status, doctor |
| `withings_api.py` | HTTPS client, OAuth exchange/refresh, getmeas pagination |
| `sync.py` | Orchestration, checkpoints, reconciliation modes |
| `storage.py` | SQLite schema, transactions, backup helpers |
| `profiles.py` | Explicit person ↔ source identity mapping + discovery reporting |

**Alternatives:** Multiple daemons; plugin architecture.

**Rationale:** Maintainability without operational complexity; systemd expects one `ExecStart` that exits.

### D3 — Minimal dependencies

**Choice:** Prefer stdlib (`sqlite3`, `http.server` for setup callback, `argparse`, `logging`, `urllib` or stdlib HTTPS). If an HTTP client is added, pin a single small dependency in `pyproject.toml` / lockfile. No ORM, no web framework.

**Alternatives:** Full `httpx`/`requests` + pydantic stack; poetry-heavy tooling.

**Rationale:** Pi unattended ops favor small, pinned surfaces and easy venv recreation on RAID.

### D4 — Config and secrets separation

**Choice:** `config/config.toml` (from `config.example.toml`) holds non-secret settings: application root, persons, DB filenames, timezone, sync overlap, callback port, client ID path references. `secrets/` (0700) holds client secret and per-identity token JSON files (0600). Token writes: temp file in `secrets/` → fsync → rename. Global process lock file under the application root (e.g. `data/.sync.lock`) serializes refresh and writers.

**Alternatives:** Single encrypted blob; OS keyring only.

**Rationale:** Fits unprivileged service account on RAID without assuming desktop keyring availability; atomic rename is portable and auditable.

### D5 — Profile discovery gate before two-person automation

**Choice:** Implement single-identity backfill/sync first. Ship a read-only `doctor`/discovery command that authorizes (or uses existing tokens), calls documented public endpoints, and reports whether a second attributable identity exists. Only after a successful discovery (or a second independently authorized identity) enable dual-person scheduled sync. If only the primary profile is visible, exit with actionable error text—never invent mappings.

**Alternatives:** Assume shared login returns both adults; heuristic split by weight.

**Rationale:** Matches R1 / acceptance criterion 11; OpenAPI does not establish a general profile-selection parameter for consumer `getmeas`, and `oauth2-listusers` is partner-restricted.

### D6 — Sync strategy: incremental weekly + periodic full reconcile

**Choice:** Default weekly run uses `lastupdate` with a small overlap window; checkpoint advances only after full page set + DB commit. Provide `backfill` / `--full` for complete history and schedule or document monthly full reconciliation. Fall back to verified weekly full-history only if live API pagination and rate limits prove cheap enough (data volume is small).

**Alternatives:** Full history every week from day one; timestamp-only upserts.

**Rationale:** Minimizes traffic while still repairing edits/deletions; upsert key is `source_identity + grpid`.

### D7 — Weight normalization with Decimal

**Choice:** Parse API `value`/`unit` with `Decimal`, compute `weight_kg = value * 10 ** unit`, store as SQLite `REAL` after validated conversion. Reject implausible weights via configurable bounds.

**Alternatives:** Float-only math at parse time.

**Rationale:** Deterministic conversion before storage; avoids binary float surprises in tests.

### D8 — Systemd: project-local units + symlinks, mount-gated

**Choice:** Store `deploy/fitness-sync.service` and `deploy/fitness-sync.timer` in-repo. Install via symlinks into `/etc/systemd/system`. Service: `User=` dedicated account, `WorkingDirectory=/mnt/raid1/apps/fitness`, `ExecStart=/mnt/raid1/apps/fitness/.venv/bin/fitness-sync sync --all`, `RequiresMountsFor=/mnt/raid1/apps/fitness`, timer `OnCalendar=Sun *-*-* 03:00:00` with `Persistent=true` and configured timezone. Application also checks mount/writability at startup (defense in depth).

**Alternatives:** User-level systemd only; long-lived daemon with in-process scheduler.

**Rationale:** Fail closed if RAID missing; OS unit paths are acknowledged SD-card exceptions; process exits when done.

### D9 — Testing without live credentials

**Choice:** Pytest (or stdlib unittest) with fixtures replaying paginated getmeas and oauth responses. Contract tests assert idempotency, same-timestamp distinct grpids, checkpoint non-advance on failure, and dry-run non-mutation. Live discovery PoC is a documented manual gate, not a CI default.

**Alternatives:** CI against real Withings sandbox only.

**Rationale:** Acceptance criterion 12; live account behavior for dual profiles remains an implementation gate.

## Risks / Trade-offs

- **[Risk] Legacy shared login exposes only one API identity** → Mitigation: blocking discovery; document independent accounts after user-approved migration or manual import; do not ship silent empty `person2` success.
- **[Risk] Localhost callback URI rejected by Withings app settings** → Mitigation: document registered redirect URI options and SSH `-L` forwarding; if localhost is disallowed, document a temporary out-of-band code paste flow without a permanent public endpoint.
- **[Risk] Unclear getmeas deletion / lastupdate boundary semantics** → Mitigation: validate against live API before enabling destructive full-snapshot deletes; prefer additive upserts until deletion semantics are confirmed.
- **[Risk] Concurrent timer + manual sync** → Mitigation: exclusive process lock; timer `Persistent=true` still safe because overlapping second run fails closed.
- **[Risk] SD card still receives systemd/journal writes** → Mitigation: document as OS exception; keep all app state on RAID; optional file logs under application `logs/`.
- **[Trade-off] Decimal parse then REAL store** → Slight precision loss at persist time is accepted for SQLite simplicity; tests assert conversion before store.
- **[Trade-off] One token set per Withings identity** → If dual profiles require two OAuth grants, config maps each local person to its identity's token file explicitly.

## Migration Plan

1. Mount and verify `/mnt/raid1`; create application root tree and service account ownership.
2. Create `.venv` on RAID; install package editable or from local tree; copy `config.example.toml` → `config.toml`; place client credentials in `secrets/`.
3. Run `authorize` (SSH tunnel if remote); run discovery; stop if dual-person attribution fails pending operator decision.
4. Run `backfill` per accessible identity; `doctor` and `status`; sample SQLite queries.
5. Symlink systemd units; `daemon-reload`; manual `systemctl start fitness-sync.service`; then enable timer.
6. Enable weekly timer only after e2e validation.

**Rollback:** Disable/stop timer and service; leave SQLite files untouched; restore from `backups/` via documented SQLite restore. No cloud schema migrations.

## Open Questions

These are implementation gates recorded in the proposal; they do not block planning artifact completion but **do** block promising dual-person automation or deletion reconciliation behavior:

1. Can standard OAuth on this legacy shared login expose both adult profiles with reliable identifiers? (read-only PoC required)
2. If not, will the owner authorize separating adults into independent Withings accounts?
3. Is a localhost callback URI accepted in the Withings developer application settings?
4. Exact live getmeas deletion semantics, pagination quirks, `lastupdate` boundaries, and rate limits—confirm before weekly full vs incremental finalization.
5. Confirm at deploy time: systemd user name, RAID permissions/mount unit, and preferred local timezone (default Sunday 03:00 assumed until then).
