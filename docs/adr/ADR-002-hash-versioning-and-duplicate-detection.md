# ADR-002: Hash, Versioning, and Duplicate Detection

## Status

Accepted. **Amended 2026-05-06** — adds a client-side pre-import
hash check (Forge widget) backed by `GET /documents/by-hash/{sha256}`
so duplicates are surfaced *before* bytes leave the browser. See the
"Pre-import precheck" section.

## Context

Filenames are unreliable identifiers. The system needs stable document identity
and duplicate detection.

## Decision

Compute SHA-256 from immutable uploaded bytes and use it as the duplicate
detection key. Store every upload as a document version. When the same hash is
uploaded again, mark the new version as `DUPLICATE_DETECTED` and link it to the
existing version.

### Pre-import precheck (#292 amendment)

The post-upload `DUPLICATE_DETECTED` flow stays as the source of
truth. On top of it, the Forge widget hashes the picked file in the
browser via `crypto.subtle.digest("SHA-256", ...)` and probes
`GET /documents/by-hash/{sha256}` *before* streaming bytes. The
route is read-only — it never mutates the catalog and never spawns
a new version — and returns
`{exists: bool, document_id, version_id, version_number,
original_filename, sha256}`.

When `exists` is `true` the widget pauses the queue row at
`DUPLICATE_DETECTED` and surfaces two operator actions:

- **Skip** — bytes never leave the browser; the catalog is
  untouched.
- **Upload anyway** — proceeds with the legacy upload flow; the
  backend creates a new version tagged `DUPLICATE_DETECTED` so the
  trace is preserved, and `ExtractionJobService.extract` blocks the
  duplicate from feeding the KG (the existing guard at the top of
  the extraction service).

The precheck is a hint, not a gate: a network blip falls back to
the legacy upload behaviour because the backend remains the
authoritative source of the duplicate flag.

## Consequences

- Duplicate detection is deterministic.
- Binary-identical uploads are traceable.
- Different files with the same filename remain distinct versions.
- **Bandwidth is preserved on duplicate uploads** (#292 amendment).
  When the operator hits Skip on the precheck banner the bytes
  never leave the browser; the catalog never sees the duplicate
  upload at all. This matters at scale and for large engineering
  drawings where re-uploading the same PDF is common.

## References

- [#292](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/292)
  §1 — Orbital UX overhaul, pre-import duplicate-detection slice.
  Source of the precheck route + Forge widget hashing flow.
