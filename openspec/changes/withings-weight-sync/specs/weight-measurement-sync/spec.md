# Spec Delta

## Purpose

Defines weight-only Withings measurement fetch, historical backfill, weekly incremental sync, full-history reconciliation, and checkpoint safety for attributable identities.

## ADDED Requirements

### Requirement: Weight-only getmeas extraction
The system MUST request Withings measurements with `action=getmeas`, `meastype=1`, and `category=1`, send `Authorization: Bearer <access_token>`, extract only measure entries with `type == 1`, and convert values to kilograms using `weight_kg = value * 10 ** unit` with deterministic decimal normalization.

#### Scenario: Type-1 measures convert to kilograms
- **WHEN** a measure group contains a type-1 entry with integer value and unit exponent
- **THEN** the stored weight equals value times ten raised to unit, expressed in kilograms

#### Scenario: Non-weight measures ignored
- **WHEN** a measure group contains only non-type-1 entries
- **THEN** no weight row is written from that group

### Requirement: Preserve measurement identity and multiplicity
The system MUST preserve original measurement timestamp as Unix UTC epoch seconds, original group ID (`grpid`), and source identity, and MUST keep multiple actual weigh-ins on the same calendar day as distinct records when their source group IDs differ.

#### Scenario: Same-day distinct groups remain distinct
- **WHEN** two measure groups share the same measured timestamp but have different `grpid` values for the same attributable identity
- **THEN** both records are stored as separate rows

### Requirement: Validation errors do not corrupt prior state
Unknown or missing required fields, non-success API status, malformed pages, and implausible weight values MUST be treated as validation errors that abort or skip unsafe writes without corrupting previously stored data.

#### Scenario: Non-success API status leaves database unchanged
- **WHEN** the Withings API returns a nonzero status for a getmeas request during sync
- **THEN** no checkpoint advances and previously stored weight rows remain intact

### Requirement: Full-history backfill with pagination guards
The system MUST support initial full-history retrieval for each accessible identity, follow `body.more` and `body.offset` until complete, and MUST guard against repeated offsets, infinite loops, and incomplete pages. Backfill MUST be resumable or safely repeatable after interruption and MUST NOT delete previously stored records based on an incomplete response.

#### Scenario: Multi-page history imports completely
- **WHEN** getmeas returns multiple pages indicated by `more` and advancing `offset`
- **THEN** all pages are fetched and all valid type-1 records are stored idempotently

#### Scenario: Interrupted backfill is safely repeatable
- **WHEN** a backfill crashes mid-pagination and is re-run
- **THEN** previously written rows are not duplicated and incomplete remote pages do not cause deletion of local history

### Requirement: Weekly sync with safe checkpoints
Scheduled sync MUST prefer incremental `lastupdate` polling for normal weekly runs plus periodic or on-demand full-history reconciliation. A checkpoint MUST be stored only after all pages and associated database changes commit successfully, using a modest overlap to avoid timestamp-boundary loss. `lastupdate` is server-side change time, not measurement timestamp.

#### Scenario: Successful incremental run advances checkpoint
- **WHEN** all pages for an identity are fetched and committed successfully
- **THEN** the sync checkpoint for that identity is updated

#### Scenario: Failed run does not advance checkpoint
- **WHEN** pagination or database commit fails mid-run
- **THEN** the previous checkpoint remains unchanged and the run is not marked successful

### Requirement: Upsert and deletion reconciliation
Upserts MUST key on authoritative source identity plus `grpid` (or a documented stronger unique identifier), not timestamp alone. For full snapshots, stale local rows MAY be removed only after the remote snapshot is demonstrably complete and attributable to the correct person. Corrected remote records MUST update local rows; verified remote deletions MUST reconcile without erasing unrelated records.

#### Scenario: Corrected weight updates existing row
- **WHEN** a remote measure group with a known `grpid` returns a different weight value
- **THEN** the local row for that `grpid` is updated and no duplicate row is created

#### Scenario: Verified remote deletion removes only matching rows
- **WHEN** a complete attributable full snapshot omits a previously stored `grpid` for that person
- **THEN** only that person's matching stale row is removed and other persons' databases are untouched

### Requirement: Per-identity error isolation
A sync invocation covering multiple configured identities MUST report errors per identity; success for one identity MUST NOT be undone by failure of another.

#### Scenario: Second identity failure keeps first success
- **WHEN** person1 sync commits successfully and person2 sync fails
- **THEN** person1 data and checkpoint remain committed and the overall process exits nonzero reporting the person2 failure
