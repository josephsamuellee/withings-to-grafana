# Spec Delta

## Purpose

Covers OAuth 2.0 authorization-code bootstrap, access-token refresh, and secure persistence of client credentials and rotating refresh tokens for Withings API access.

## ADDED Requirements

### Requirement: Authorization-code bootstrap with metrics scope
The system MUST perform the Withings OAuth 2.0 authorization-code flow with the `user.metrics` scope and a registered callback URI, exchanging the authorization code promptly after redirect.

#### Scenario: Interactive authorize completes code exchange
- **WHEN** the operator runs the authorize command and completes the Withings consent redirect within the code validity window
- **THEN** the system exchanges the code for access and refresh tokens and persists them without leaving a permanent HTTP listener running

### Requirement: Short-lived setup callback only
A temporary localhost HTTP callback listener is permitted only during interactive setup and MUST terminate after success or failure; it MUST NOT be part of the weekly sync service.

#### Scenario: Callback stops after authorize
- **WHEN** authorization succeeds or fails during setup
- **THEN** the localhost callback listener exits and no listener remains for scheduled sync runs

### Requirement: One token set per authorized Withings identity
The system MUST store OAuth client credentials securely and MUST store access token, refresh token, and expiry for each independently authorized Withings identity—not automatically one token set per local person.

#### Scenario: Shared login yields one token set
- **WHEN** a single OAuth authorization covers one Withings identity
- **THEN** exactly one token set is persisted for that identity regardless of how many local persons are configured

### Requirement: Access-token refresh before API use
Before weekly or on-demand API access, the system MUST refresh the access token when expired or near expiry using the documented Withings refresh endpoint and grant type, and MUST persist any replacement refresh token atomically.

#### Scenario: Expired access token is refreshed
- **WHEN** a sync run starts and the stored access token is expired or within the configured refresh window
- **THEN** the system obtains a new access token via refresh_token grant and writes updated tokens via temp-file + rename under the secrets directory

### Requirement: Concurrent refresh protection and no secret logging
The system MUST avoid concurrent refresh races with a process lock, MUST NEVER print or log tokens or authorization codes, and MUST restrict secret file permissions appropriately.

#### Scenario: Secrets not emitted in logs
- **WHEN** authorize or sync runs with verbose logging enabled
- **THEN** logs contain no access tokens, refresh tokens, authorization codes, or client secrets

### Requirement: Revoked authorization fails visibly
If authorization is revoked or refresh fails permanently, the system MUST fail visibly, require interactive reauthorization, and MUST NOT retry indefinitely.

#### Scenario: Invalid refresh token aborts run
- **WHEN** the refresh endpoint rejects the refresh token as invalid or revoked
- **THEN** the process exits with a nonzero status instructing interactive reauthorization and does not enter an unbounded retry loop
