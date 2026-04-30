# ADR-008: SemanticDocument Schema Versioning Policy

## Status

Accepted

## Context

`SemanticDocument.schema_version` was a free-form string (`"v0.1"` today).
The catalog persists the full `SemanticDocument` as JSON in the
`semantic_documents.payload` column. As the schema evolves, old payloads on
disk may not load against newer code without an explicit migration path,
and a free-form `str` field offers no compile-time signal that a payload's
shape is supported by the running build.

## Decision

1. **Versioning scheme.** `SemanticDocument.schema_version` follows
   `vMAJOR.MINOR` (e.g. `v0.1`, `v0.2`, `v1.0`).
   - **Major bump** = breaking change: a field is removed, a field's type
     changes, or the semantics of an existing field change.
   - **Minor bump** = additive change: a new optional field, or a new
     enum value on an existing field.
2. **Typed enumeration.** `SemanticDocument.schema_version` becomes a
   `Literal[...]` listing every supported version. Persisted payloads
   declaring an unknown value are rejected at the loader boundary, not
   silently coerced.
3. **Single read boundary.** A new
   `app.services.semantic_schema_loader.load_semantic_document(payload)`
   is the only place where a persisted JSON payload becomes a typed
   `SemanticDocument`. It dispatches on `schema_version` against a
   `MIGRATORS` registry:
   - The current version maps to identity.
   - Older versions map to functions that return a current-shape payload.
   - Unknown future versions raise `UnsupportedSchemaVersion` (a
     `ValueError` subclass).
4. **Catalog split.** `CatalogStore` exposes both
   `get_semantic_document(version_id)` (typed, routed through the loader)
   and `get_semantic_document_payload(version_id)` (raw dict, the bytes
   on disk). Services that need a typed model call the loader explicitly
   so the migration path is unambiguous.
5. **Initial state.** Only `v0.1` exists. The current code is its own
   migrator (identity). A hand-written `v0.1` fixture lives at
   `apps/api/tests/fixtures/semantic_v0_1.json` and is loaded by the
   loader's tests.

## Consequences

- Every schema change requires:
  1. A bump to `SemanticDocument.schema_version`'s `Literal[...]`.
  2. A migrator entry in `MIGRATORS` (`noop` for additive minor bumps that
     remain readable as the previous shape, otherwise a real conversion).
  3. A fixture under `apps/api/tests/fixtures/semantic_vMAJOR_MINOR.json`
     and a test that loads it.
  4. A CHANGELOG entry calling out the version bump and the migration.
- Old payloads keep loading after the schema evolves; payloads from a
  future build raise a typed error rather than silently passing through.
- `SemanticOutputService.get` and `generate` no longer return the same
  Python instance across calls — the loader rebuilds the model on every
  read. Callers must compare by content (or by `id`), not by identity.
