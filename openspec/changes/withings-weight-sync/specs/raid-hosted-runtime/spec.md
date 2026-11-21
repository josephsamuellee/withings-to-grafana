# Spec Delta

## Purpose

Constrains deployment so the application root, virtualenv, config, secrets, databases, logs, and backups live under the RAID path, with systemd running short-lived weekly jobs that fail closed if the mount is missing.

## ADDED Requirements

### Requirement: Single configured application root on RAID
The application root MUST be `/mnt/raid1/apps/fitness` (or an explicitly configured absolute path under that mount for this deployment). Project source, virtualenv, config, secrets, SQLite databases, optional file logs, temporary working files, and application-created backups MUST live under this root. Runtime paths MUST derive from that root rather than hardcoded `/opt`, `/home/pi`, or CWD-relative paths.

#### Scenario: No /opt application paths
- **WHEN** installation and configuration are complete
- **THEN** no application-managed path for source, venv, config, secrets, data, logs, or backups references `/opt`

### Requirement: Fail closed when RAID is unavailable
The application MUST refuse to run or write application state if the RAID mount for the application root is unavailable, and MUST NOT create or fall back to a same-named directory on the root filesystem.

#### Scenario: Missing mount aborts startup
- **WHEN** `/mnt/raid1/apps/fitness` is not an available mounted path
- **THEN** CLI and service invocations exit nonzero without writing state under `/mnt/raid1` on the root filesystem

### Requirement: Systemd unit and timer under project deploy
Deployment MUST provide a systemd service and weekly timer. Prefer unit files stored under the project `deploy/` directory with symlinks in `/etc/systemd/system` (or user-systemd where appropriate). The service MUST set `WorkingDirectory` to the application root, an absolute `ExecStart` into the project virtualenv, and a mount dependency such as `RequiresMountsFor` for the application root. `Persistent=true` MUST be set on the timer so missed runs catch up after downtime.

#### Scenario: Weekly timer invokes short-lived process
- **WHEN** the timer fires
- **THEN** a single short-lived Python process runs sync and exits when finished rather than remaining as a long-lived polling daemon

#### Scenario: Units reference RAID paths
- **WHEN** the service unit is installed
- **THEN** `WorkingDirectory` and `ExecStart` point under `/mnt/raid1/apps/fitness` and include a mount requirement for that path

### Requirement: Unprivileged service account and secret permissions
The service MUST run as a dedicated unprivileged account with read/write access on the RAID application tree. Secrets directory MUST be mode `0700` and token files mode `0600` (or equivalent restrictive ownership), readable by that account only among non-root users.

#### Scenario: Secrets directory permissions
- **WHEN** secrets are installed for the service account
- **THEN** the secrets directory is owner-accessible only and token files are not group- or world-readable

### Requirement: Documented install and recovery procedures
Documentation MUST cover RAID mount verification, creating the virtualenv on the RAID drive, installing dependencies there, systemd activation, a manual test run, backup/restore, and disaster recovery. Documentation MUST note that the system Python interpreter and OS/systemd binaries may remain system-installed.

#### Scenario: Fresh Pi install stays on RAID
- **WHEN** an operator follows the documented install on a fresh Pi with RAID mounted
- **THEN** application files and state reside under `/mnt/raid1/apps/fitness` and the documented commands work after reboot with the RAID mounted

### Requirement: Default weekly schedule
The default timer schedule MUST run once per week at Sunday 03:00 in the configured local timezone unless overridden at deployment.

#### Scenario: Default Sunday morning schedule
- **WHEN** the timer is installed with default configuration
- **THEN** it is scheduled for weekly execution at Sunday 03:00 in the configured timezone
