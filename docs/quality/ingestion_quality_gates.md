# Ingestion Quality Gates

## Catalog and Hashing

- Pass: SHA-256 is computed from immutable file bytes.
- Pass: same binary file produces the same hash.
- Pass: duplicate upload is detected by hash.
- Pass: duplicate detection does not rely on filename.
- Pass: raw file storage URI is persisted.
- Fail: upload succeeds without a hash.
- Fail: duplicate upload silently creates an unrelated document.

## Extraction Pipeline

- Pass: extraction status moves from `STORED` to `EXTRACTING` to `EXTRACTED`.
- Pass: parser name and parser version are recorded.
- Pass: raw extraction JSON is stored.
- Pass: parser failures are persisted with error messages.
- Fail: parser error is swallowed.
- Fail: raw extraction output cannot be inspected.

## Semantic JSON

- Pass: semantic JSON is schema-validated before storage.
- Pass: low-confidence assets are flagged.
- Pass: unsupported claims are marked `needs_review`.
- Pass: missing source lineage creates a warning.
- Fail: invalid semantic JSON is stored as successful output.
- Fail: inferred claims are marked as trusted without review.

## Markdown Output

- Pass: one Markdown file is generated per document version.
- Pass: YAML frontmatter includes required metadata.
- Pass: source lineage section is included.
- Fail: Markdown omits hash, parser, or validation status.
