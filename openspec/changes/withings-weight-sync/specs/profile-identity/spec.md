# Spec Delta

## Purpose

Defines stable local person identities and explicit Withings source mappings so measurements are never silently attributed by weight heuristics or shared-login assumptions.

## ADDED Requirements

### Requirement: Stable local person identifiers
The system MUST maintain exactly two configured local person identifiers (`person1` and `person2`) and associate each with its own SQLite database path under the configured application root.

#### Scenario: Configured persons map to separate databases
- **WHEN** the application loads a valid configuration
- **THEN** each of `person1` and `person2` resolves to a distinct database file path under the application data directory

### Requirement: Explicit Withings source mapping
The system MUST record an authoritative Withings user or profile identifier for each accessible source identity during setup when the API provides one, and MUST refuse to invent or infer that identifier from measurement values.

#### Scenario: Source identifier recorded at setup
- **WHEN** setup successfully binds a local person to an authorized Withings identity that exposes a documented user or profile ID
- **THEN** that identifier is persisted in configuration or mapping state for later sync attribution

#### Scenario: No measurement-based assignment
- **WHEN** a measurement arrives without a reliable source-to-person mapping
- **THEN** the system does not assign it to a person based on measured weight, name inference, or other heuristics

### Requirement: Blocking profile-access discovery gate
Before enabling automatic two-person ingestion, the system MUST support a read-only discovery operation against the real authorized account that reports whether both adults' measurements can be retrieved and attributed reliably through the public API.

#### Scenario: Discovery reports dual-profile access
- **WHEN** the operator runs the read-only discovery test and the API returns reliably attributable measurements for both adults
- **THEN** the system reports success with the discovered identifiers and permits configuring two-person sync

#### Scenario: Discovery fails for second adult
- **WHEN** the public API exposes only the primary profile or cannot distinguish both adults reliably
- **THEN** the system fails with an actionable error documenting supported alternatives (independently authorized accounts after user-approved migration, or a clearly labeled manual import path if available)

### Requirement: No silent empty second database success
If the second person cannot be reliably sourced, the system MUST NOT create or leave an empty second database and report overall success for two-person sync.

#### Scenario: Incomplete dual-person setup is a failure
- **WHEN** automatic two-person sync is requested but only one identity is reliably attributable
- **THEN** the run exits with a nonzero status and does not claim both persons synced successfully

### Requirement: No automatic profile migration
The system MUST NOT migrate, delete, merge, or alter Withings account profiles automatically.

#### Scenario: Operator must approve account separation
- **WHEN** dual-profile access is unavailable on the legacy shared login
- **THEN** any separation into independent Withings accounts remains a manual, user-approved action outside the application
