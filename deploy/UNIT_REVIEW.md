# Systemd unit static review checklist

Use when `systemd-analyze verify` is unavailable (e.g. macOS dev host).

- [x] `WorkingDirectory=/mnt/raid1/apps/fitness`
- [x] Absolute `ExecStart` into project `.venv`
- [x] `RequiresMountsFor=/mnt/raid1/apps/fitness`
- [x] Dedicated unprivileged `User=` / `Group=`
- [x] Timer `OnCalendar=Sun *-*-* 03:00:00` and `Persistent=true`
- [x] No `/opt` paths
- [x] Service is `Type=oneshot` (short-lived; not a long-lived poller)

On a Linux host with systemd:

```bash
systemd-analyze verify /mnt/raid1/apps/fitness/deploy/fitness-sync.service
systemd-analyze verify /mnt/raid1/apps/fitness/deploy/fitness-sync.timer
```
