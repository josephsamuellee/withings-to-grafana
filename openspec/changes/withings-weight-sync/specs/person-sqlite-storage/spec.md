# Spec Delta

## Purpose

Specifies per-person SQLite persistence for weight measurements, sync checkpoints, run history, concurrency control, and database-consistent backups under the application root.

## ADDED Requirements

### Requirement: One SQLite database per local person
The system MUST maintain exactly one SQLite database file per configured local person and MUST NOT require PostgreSQL or another shared multi-tenant database for weight storage.

#### Scenario: Two persons use two files
- **WHEN** both `person1` and `person2` are configured
- **THEN** measurements for each person are stored only in that person's SQLite file

### Requirement: Weight measurements schema
Each person database MUST provide a `weight_measurements` table with primary key `source_grpid`, non-null `measured_at_utc`, non-null `weight_kg`, optional `source_updated_at_utc`, and non-null `fetched_at_utc`, plus an index on `measured_at_utc`.

#### Scenario: Insert stores required columns
- **WHEN** a valid weight measurement is upserted
- **THEN** the row stores `source_grpid`, UTC measurement epoch, weight in kilograms, fetch time, and leaves `source_updated_at_utc` null when the API does not expose a per-group update timestamp

### Requirement: Sync state and run history tables
Each person database MUST provide `sync_state` (key/value) and `sync_runs` (started/finished times, status, fetched count, optional error summary) tables for checkpoints and operational history.

#### Scenario: Run recorded on sync
- **WHEN** a sync for a person starts and later finishes successfully
- **THEN** a `sync_runs` row records start time, finish time, success status, and fetched count

### Requirement: UTC storage without daily collapse
Timestamps MUST be stored in UTC. The system MUST NOT enforce one row per calendar day at ingestion time; local calendar-day conversion belongs to queries, not writes.

#### Scenario: Multiple same-day weigh-ins stored
- **WHEN** two distinct groups occur on the same UTC calendar day
- **THEN** both rows remain stored without being collapsed into a single daily row

### Requirement: Transactional writes and writer exclusion
The system MUST use transactions for multi-row updates, MAY enable WAL mode, and MUST prevent overlapping writers and overlapping scheduled runs from competing for the same SQLite files or token refresh.

#### Scenario: Second concurrent writer is rejected or waits safely
- **WHEN** a sync process already holds the application lock for a person or global run
- **THEN** a second overlapping sync does not corrupt SQLite state or double-refresh tokens

### Requirement: Database-consistent backups
Backups MUST use the SQLite backup API or `VACUUM INTO` (or equivalent safe mechanism) rather than an unsafe live file copy of a potentially open database.

#### Scenario: Backup produces readable snapshot
- **WHEN** the operator requests a backup while the database may have been recently written
- **THEN** the backup file under the application backups directory opens as a consistent SQLite database
