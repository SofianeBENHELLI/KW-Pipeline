# ADR-002: Hash, Versioning, and Duplicate Detection

## Status

Accepted

## Context

Filenames are unreliable identifiers. The system needs stable document identity
and duplicate detection.

## Decision

Compute SHA-256 from immutable uploaded bytes and use it as the duplicate
detection key. Store every upload as a document version. When the same hash is
uploaded again, mark the new version as `DUPLICATE_DETECTED` and link it to the
existing version.

## Consequences

- Duplicate detection is deterministic.
- Binary-identical uploads are traceable.
- Different files with the same filename remain distinct versions.
