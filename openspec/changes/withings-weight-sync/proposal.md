# Proposal

## Why

The household needs an unattended, self-hosted service on a Raspberry Pi that imports historical Withings weight measurements and keeps them synchronized weekly—without a web portal, without writing application state to the SD card, and without guessing which adult profile produced each reading. A fresh Python CLI under `/mnt/raid1/apps/fitness` is the right fit: relocatable, short-lived per run, and maintainable for long-term RAID-hosted operation.

## What Changes

- Introduce a Python CLI package (`fitness_sync`) for OAuth bootstrap, historical backfill, weekly sync, diagnostics, and optional reauthorization.
- Persist weight-only measurements (Withings type 1, kilograms) into one SQLite database per logical person (`person1`, `person2`).
- Enforce explicit profile-to-source mapping after a read-only discovery gate; never assign measurements by weight heuristics.
- Manage OAuth 2.0 authorization-code flow, access-token refresh, and atomic refresh-token persistence under a private `secrets/` tree.
- Deploy via systemd service + timer rooted on the RAID mount, failing closed when the mount is unavailable.
- Document first-run, verification, backup/restore, and disaster-recovery procedures.
- Add mocked-API tests covering pagination, idempotency, token refresh, and failure isolation.

## Capabilities

### New Capabilities

- `profile-identity`: Stable local person IDs, explicit Withings source mapping, and blocking discovery when both adult profiles cannot be reliably distinguished.
- `oauth-token-management`: Authorization-code bootstrap, short-lived localhost callback (setup only), token refresh, atomic secret storage, and fail-visible reauthorization.
- `weight-measurement-sync`: getmeas weight extraction, pagination/backfill, incremental and full reconciliation, checkpoints, and per-identity error isolation.
- `person-sqlite-storage`: Per-person SQLite schema, WAL/transactions, upsert-by-grpid, sync state/runs, and safe backups.
- `sync-cli-ops`: CLI surface (`authorize`, `sync`, `backfill`, `status`, `doctor`), dry-run, logging, exit codes, and HTTP retry behavior.
- `raid-hosted-runtime`: Application root under `/mnt/raid1/apps/fitness`, systemd unit/timer layout, mount safeguards, and unprivileged service account constraints.

### Modified Capabilities

- (none — greenfield project; `openspec/specs/` has no existing capabilities)

## Impact

- **Code**: New self-contained package under the proposed RAID layout; does not extend or depend on any prior `/opt` or web-portal prototype.
- **External API**: Withings OpenAPI 3.0.3 / API v2.0 (`oauth2`, `measure`/`getmeas`); profile isolation via shared legacy login is an integration gate, not assumed.
- **Runtime**: Raspberry Pi 4 Linux, RAID mount, systemd timer (weekly), dedicated unprivileged user; OS unit symlinks under `/etc/systemd/system` are the only intentional SD-card exception.
- **Dependencies**: Minimal Python stack (stdlib preferred; small pinned HTTP client optional); SQLite only—no PostgreSQL, Grafana, or always-on app server.
- **Data**: Separate SQLite files remain queryable by a future visualization layer; weight records only.
