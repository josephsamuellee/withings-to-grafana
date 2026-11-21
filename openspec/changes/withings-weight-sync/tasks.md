# Tasks

## 1. Package skeleton and relocatable layout

- [x] 1.1 Create `pyproject.toml` (or pinned requirements + lockfile), `src/fitness_sync/` package with `__init__.py` and CLI entry point, `tests/`, `config/config.example.toml`, and empty `secrets/`, `data/`, `backups/`, `logs/`, `deploy/` directories matching the proposed RAID layout; verify the package imports via `python -c "import fitness_sync"` from a fresh venv.
- [x] 1.2 Implement application-root path resolution (config absolute root, derive data/secrets/logs/backups) and mount/writability checks that refuse to create fallback dirs on the root filesystem; verify unit tests fail closed when the root path is missing or not writable as a mount.
- [x] 1.3 Add README sections for layout, install on RAID, and “no /opt paths”; verify docs mention `/mnt/raid1/apps/fitness` and systemd symlink exception only.

## 2. SQLite storage

- [x] 2.1 Implement `storage.py` schema creation for `weight_measurements`, `sync_state`, and `sync_runs` with WAL/transactions helpers; verify schema tests create one DB per person path.
- [x] 2.2 Implement upsert-by-`source_grpid`, sync checkpoint get/set (commit only on success paths), and `sync_runs` recording; verify idempotent double-upsert and same-timestamp distinct `grpid` tests pass.
- [x] 2.3 Implement SQLite backup API or `VACUUM INTO` into `backups/`; verify backup file opens and row counts match.

## 3. OAuth and Withings API client

- [x] 3.1 Implement authorization-code bootstrap with short-lived localhost callback, `user.metrics` scope, prompt code exchange, and callback shutdown on success/failure; verify mocked authorize test completes without a leftover listener thread/process.
- [x] 3.2 Implement atomic token persistence under `secrets/` (0600 files), process lock around refresh, and refresh via `POST .../v2/oauth2` `action=requesttoken` `grant_type=refresh_token`; verify mocked expiry refresh updates tokens and never logs secrets.
- [x] 3.3 Implement HTTPS getmeas client with Bearer auth, timeouts, exponential backoff retries, pagination (`more`/`offset`) guards, and Decimal weight conversion for type 1 / category 1; verify mocked multi-page fixture yields correct kg values and ignores non-type-1 measures.

## 4. Single-identity sync and backfill

- [x] 4.1 Implement `backfill` orchestration (full history, optional date windows, resumable/idempotent writes, no delete on incomplete pages); verify interrupted-then-rerun fixture leaves no duplicates and preserves prior rows.
- [x] 4.2 Implement incremental sync with `lastupdate` overlap, checkpoint advance only after full commit, and `--dry-run` that skips DB/checkpoint writes while disclosing possible token refresh; verify failure mid-pagination does not advance checkpoint.
- [x] 4.3 Wire CLI commands `authorize`, `backfill`, `sync --person`, `status` for single identity; verify `--help` and dry-run/status tests pass without real credentials.

## 5. Profile identity discovery gate

- [x] 5.1 Implement `profiles.py` config for `person1`/`person2`, explicit source-identity mapping fields, and refusal to assign by weight heuristics; verify mapping-required tests reject unmapped writes.
- [x] 5.2 Implement read-only discovery/doctor checks that report whether dual adults are reliably attributable via the public API; verify mocked “primary-only” response produces actionable nonzero failure (no silent empty second DB success).
- [x] 5.3 Document supported alternatives (independent accounts after user-approved migration; labeled manual import if available) and that profile migration is never automated; verify README/doctor messaging matches.
- [ ] 5.4 **Gate:** Run live read-only discovery against the legacy shared login before enabling automatic two-person sync; record outcome in ops notes. If dual attribution fails, stop dual-person automation work and proceed only with accessible identities / operator decision—verify by documented PoC result, not CI.

## 6. Multi-person reconciliation and isolation

- [x] 6.1 Implement `sync --all` with per-identity error isolation (person1 success retained if person2 fails); verify integration test with one mocked success and one failure.
- [x] 6.2 Implement full-snapshot reconciliation that updates corrected grpids and deletes stale local rows only after a demonstrably complete attributable snapshot; verify correction/deletion fixtures and “never delete from incomplete response” tests.
- [x] 6.3 Add periodic/on-demand `--full` reconcile path and document weekly incremental + monthly/full strategy; verify CLI flag and checkpoint behavior tests.

## 7. CLI ops hardening

- [x] 7.1 Complete `doctor` checks: RAID/root mount, permissions, config, schema, clock, token presence, profile mappings without secret leakage; verify doctor tests cover missing mount and missing tokens.
- [x] 7.2 Add structured logging (person id, counts, elapsed, errors; no weights/secrets by default) and useful nonzero exit codes; verify log-capture tests.
- [x] 7.3 Document SSH port forwarding for remote Pi authorize and callback URI registration constraints; verify docs include a concrete `ssh -L` example.

## 8. Systemd and RAID deployment

- [x] 8.1 Author `deploy/fitness-sync.service` and `deploy/fitness-sync.timer` with `WorkingDirectory`, absolute venv `ExecStart`, `RequiresMountsFor=/mnt/raid1/apps/fitness`, dedicated `User=`, Sunday 03:00 local default, `Persistent=true`; verify unit files parse (`systemd-analyze verify` on a Linux host when available, or static review checklist in CI notes).
- [x] 8.2 Document service account creation, `secrets/` 0700 / token 0600 permissions, venv creation on RAID, symlink install into `/etc/systemd/system`, manual test run, enable timer, backup/restore, and disaster recovery; verify README procedure references only RAID application paths (no `/opt`).
- [x] 8.3 Add restore procedure using SQLite backup artifacts; verify documented restore commands reconstruct a readable person DB in a dry lab path.

## 9. End-to-end validation

- [x] 9.1 Run full mocked test suite (OAuth, pagination, idempotency, checkpoint safety, dry-run, isolation) and verify all tests pass without real credentials.
- [ ] 9.2 After successful discovery gate (task 5.4), perform live backfill/sync validation for each authorized identity, confirm acceptance criteria 1–12 against the Pi+RAID environment, then enable the weekly timer; verify with `systemctl status` / timer list and SQLite spot checks.
