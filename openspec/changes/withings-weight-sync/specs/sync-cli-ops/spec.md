# Spec Delta

## Purpose

Defines the operator-facing CLI for authorization, sync, backfill, status, diagnostics, dry-run behavior, logging, retries, and exit codes without exposing secrets or health record contents.

## ADDED Requirements

### Requirement: Required CLI commands
The system MUST provide CLI commands that authorize an identity, synchronize all or one person, backfill history, report status, and run doctor diagnostics (exact subcommand spellings may vary).

#### Scenario: Sync all persons
- **WHEN** the operator invokes sync for all configured persons with valid tokens and mappings
- **THEN** each attributable person is processed and a summary of outcomes is returned

#### Scenario: Status reports without secrets
- **WHEN** the operator runs status
- **THEN** the output includes per-person sync health indicators without printing tokens or raw credentials

### Requirement: Doctor diagnostics
The `doctor` command MUST validate RAID mount presence for the application root, write permissions, configuration load, schema presence, clock sanity, token file presence, and known profile mappings without exposing secret values.

#### Scenario: Missing RAID mount fails doctor
- **WHEN** the configured application root is not on an available mount
- **THEN** doctor reports a failing check and exits nonzero

#### Scenario: Doctor never prints secrets
- **WHEN** doctor inspects token files that exist
- **THEN** output confirms presence or absence without printing token contents

### Requirement: Dry-run disclosure
The system MUST support a `--dry-run` mode that fetches and validates remote data without modifying SQLite databases or checkpoints, and MUST disclose that token refresh may still occur when required for API access.

#### Scenario: Dry-run leaves databases unchanged
- **WHEN** sync is run with `--dry-run` against a reachable API
- **THEN** SQLite measurement tables and sync checkpoints are not modified, and the operator is informed if token refresh may occur

### Requirement: Bounded HTTP behavior and exit codes
HTTP calls MUST use bounded timeouts and retries with exponential backoff for transient failures and rate limiting. Processes MUST exit with useful nonzero codes on failure.

#### Scenario: Transient rate limit is retried then succeeds
- **WHEN** the API returns a transient rate-limit or network error that later recovers within retry budget
- **THEN** the client retries with backoff and completes without treating the transient error as permanent failure

#### Scenario: Exhausted retries exit nonzero
- **WHEN** retries are exhausted for a required API call
- **THEN** the process exits nonzero without advancing checkpoints

### Requirement: Structured operational logging without PII payloads
Logs (structured files and/or journald) MUST include person identifier, record counts, elapsed time, and errors, and MUST NOT log health record payloads or secrets by default.

#### Scenario: Successful sync log summary
- **WHEN** a person sync completes successfully
- **THEN** logs include the person id, fetched/upserted counts, elapsed time, and success status without logging individual weight values by default

### Requirement: HTTPS only for Withings API
All Withings API requests MUST use HTTPS. The local OAuth callback MUST not be exposed beyond interactive setup.

#### Scenario: API base URL is HTTPS
- **WHEN** the client issues oauth or measure requests
- **THEN** the request URL uses the `https` scheme to Withings endpoints
