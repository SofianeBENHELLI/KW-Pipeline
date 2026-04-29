# KW Pipeline

KW Pipeline is a document intelligence MVP focused on auditable ingestion,
deterministic parsing, governed semantic extraction, and reviewable Markdown
outputs.

The first implementation target is intentionally narrow:

- upload and catalog documents;
- compute immutable SHA-256 hashes;
- detect duplicate binary uploads;
- preserve document version lineage;
- parse raw document content into inspectable extraction JSON;
- transform raw extraction into schema-validated semantic JSON;
- generate one Markdown asset per document version;
- keep all unverified semantic claims in `needs_review`.

See `docs/architecture/document_intelligence_mvp.md` for the initial contract.
