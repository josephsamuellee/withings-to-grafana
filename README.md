# fitness-sync

Self-hosted Withings **weight** synchronizer for a Raspberry Pi 4 with data on an external RAID mount. Short-lived Python CLI + weekly `systemd` timer. No web portal, no Grafana requirement, no PostgreSQL.

Application root (non-negotiable for production):

```text
/mnt/raid1/apps/fitness
```

All project source, virtualenv, config, secrets, SQLite databases, optional file logs, temp files, and application backups live under this root. **Do not install application state under `/opt` or on the SD card.** OS-managed exceptions: systemd unit symlinks under `/etc/systemd/system` and journald.

## Layout

```text
/mnt/raid1/apps/fitness/
├── README.md
├── pyproject.toml
├── src/fitness_sync/
├── tests/
├── config/
│   ├── config.example.toml
│   └── config.toml              # created by operator
├── secrets/                     # mode 0700; token files 0600
├── data/
│   ├── person1.sqlite3
│   └── person2.sqlite3
├── backups/
├── logs/                        # optional; journald is fine
├── .venv/
└── deploy/
    ├── fitness-sync.service
    └── fitness-sync.timer
```

## Install on RAID (fresh Pi)

1. Mount and verify RAID:

   ```bash
   findmnt /mnt/raid1
   sudo mkdir -p /mnt/raid1/apps/fitness
   ```

2. Create an unprivileged service account and ownership:

   ```bash
   sudo useradd --system --home /mnt/raid1/apps/fitness --shell /usr/sbin/nologin fitness
   sudo chown -R fitness:fitness /mnt/raid1/apps/fitness
   ```

3. Copy this repository onto the RAID root (as `fitness` or with correct ownership). Create the venv **on the RAID drive**:

   ```bash
   cd /mnt/raid1/apps/fitness
   python3 -m venv .venv
   .venv/bin/pip install -U pip
   .venv/bin/pip install -e ".[dev]"
   ```

   System Python and systemd remain OS-installed; only the app venv lives on RAID.

4. Config and secrets:

   ```bash
   cp config/config.example.toml config/config.toml
   # edit config.toml — keep app.root = "/mnt/raid1/apps/fitness"
   chmod 700 secrets
   printf '%s' 'YOUR_CLIENT_ID' > secrets/client_id
   printf '%s' 'YOUR_CLIENT_SECRET' > secrets/client_secret
   chmod 600 secrets/client_id secrets/client_secret
   ```

5. Authorize (interactive, one-time). Register a Withings OAuth redirect URI such as `http://127.0.0.1:8765/callback`. From a laptop, forward the port:

   ```bash
   ssh -L 8765:127.0.0.1:8765 pi@<raspberry-pi-host>
   ```

   On the Pi (or via the tunnel):

   ```bash
   export FITNESS_SYNC_CONFIG=/mnt/raid1/apps/fitness/config/config.toml
   /mnt/raid1/apps/fitness/.venv/bin/fitness-sync authorize --person person1
   ```

   The localhost callback listener **stops after success/failure** and is not part of the weekly service. Authorization codes are short-lived (~30s); exchange is immediate. Tokens are never logged.

6. Set `persons.person1.identity_id` from the printed Withings userid. Run discovery before enabling a second person:

   ```bash
   fitness-sync discover --person person1
   fitness-sync doctor
   ```

## Profile isolation (blocking gate)

One physical scale and a legacy shared Withings login with two adult profiles is **not** assumed to expose both identities through the public API. `oauth2-listusers` is partner-restricted; consumer `getmeas` has no general profile-selection parameter.

- Never assign measurements by weight ranges or name heuristics.
- Never migrate, delete, or alter Withings account profiles automatically.
- If discovery finds only the primary profile, the tool fails with an actionable error. Supported alternatives:
  1. **Independently authorized accounts** after **user-approved** migration of adult profiles.
  2. A **clearly labeled manual import** for the second person, if you export data yourself.
- Do not leave `person2` enabled / `dual_person_sync_enabled = true` until attribution is proven. Record live PoC results in `ops/discovery-notes.md`.

## Sync strategy

- Weekly timer: incremental `lastupdate` polling with overlap.
- On demand / periodic: `fitness-sync sync --all --full` for full-history reconcile (updates + verified deletions).
- Prefer monthly full reconcile once live pagination/rate limits are confirmed.

```bash
fitness-sync backfill --person person1
fitness-sync sync --person person1
fitness-sync sync --all
fitness-sync sync --all --full
fitness-sync sync --all --dry-run   # discloses that token refresh may still occur
fitness-sync status
fitness-sync doctor
fitness-sync backup --person person1
```

## systemd

Unit files live under the project; symlink into `/etc/systemd/system` (OS path exception):

```bash
sudo ln -sf /mnt/raid1/apps/fitness/deploy/fitness-sync.service /etc/systemd/system/
sudo ln -sf /mnt/raid1/apps/fitness/deploy/fitness-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start fitness-sync.service    # manual test
sudo systemctl status fitness-sync.service
sudo systemctl enable --now fitness-sync.timer
systemctl list-timers fitness-sync.timer
```

Units set `WorkingDirectory` and absolute `ExecStart` under `/mnt/raid1/apps/fitness`, plus `RequiresMountsFor=/mnt/raid1/apps/fitness`. If RAID is unavailable, the job fails closed (no fallback directory on the root filesystem).

Default schedule: **Sunday 03:00** local time, `Persistent=true`.

## Backup and restore

Create a consistent backup (SQLite backup API, not a live `cp` of an open DB):

```bash
fitness-sync backup --person person1
# writes e.g. backups/person1-YYYYMMDDThhmmssZ.sqlite3
```

Restore (disaster recovery / dry lab):

```bash
# Stop the timer/service first on the Pi
sudo systemctl stop fitness-sync.timer fitness-sync.service

# Example: restore into data/ from a backup artifact
cp /mnt/raid1/apps/fitness/backups/person1-XXXX.sqlite3 \
   /mnt/raid1/apps/fitness/data/person1.sqlite3

# Verify
sqlite3 /mnt/raid1/apps/fitness/data/person1.sqlite3 \
  'SELECT COUNT(*) FROM weight_measurements;'
```

Dry-lab verification on any machine:

```bash
mkdir -p /tmp/fitness-restore-lab
cp backups/person1-XXXX.sqlite3 /tmp/fitness-restore-lab/person1.sqlite3
sqlite3 /tmp/fitness-restore-lab/person1.sqlite3 '.tables'
sqlite3 /tmp/fitness-restore-lab/person1.sqlite3 \
  'SELECT source_grpid, measured_at_utc, weight_kg FROM weight_measurements LIMIT 5;'
```

## Development / tests

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
FITNESS_SYNC_SKIP_MOUNT_CHECK=1 .venv/bin/pytest -q
```

Tests use mocked OAuth and paginated Withings responses; no real credentials required.

## Commands after reboot

With RAID mounted:

```bash
findmnt /mnt/raid1
export FITNESS_SYNC_CONFIG=/mnt/raid1/apps/fitness/config/config.toml
/mnt/raid1/apps/fitness/.venv/bin/fitness-sync doctor
/mnt/raid1/apps/fitness/.venv/bin/fitness-sync status
sudo systemctl start fitness-sync.service
```
