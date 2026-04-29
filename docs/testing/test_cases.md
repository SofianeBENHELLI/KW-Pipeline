# Test Cases

## Backend Unit Tests

Run backend tests with:

```bash
.venv312/bin/python -m pytest apps/api/tests
```

| Area | Test Case | Expected Result |
| --- | --- | --- |
| Hashing | Same bytes are hashed twice. | SHA-256 digest is stable. |
| Hashing | One byte changes. | SHA-256 digest changes. |
| Upload service | Store a new document. | Metadata, storage URI, size, hash, and `STORED` status are persisted. |
| Upload service | Upload identical bytes with a different filename. | New version is marked `DUPLICATE_DETECTED` and links to the original version. |
| Parser | Parse text with blank lines. | Non-empty lines produce source references with line numbers. |
| Parser | Parse whitespace-only content. | No source references are emitted and a warning is recorded. |
| Filesystem storage | Attempt parent traversal in storage key. | Storage rejects the key. |
| Filesystem storage | Attempt to read a `file://` URI outside the storage root. | Storage rejects the URI. |
| SQLite catalog | Restart services with the same data directory. | Document and version metadata remain available. |
| SQLite catalog | Upload duplicate bytes after restart. | Duplicate detection still links to the original version. |
| SQLite catalog | Update lifecycle status and restart services. | Updated status remains persisted. |
| SQLite catalog | Look up missing document/version records. | Clear `KeyError` messages identify the missing resource. |
| Semantic schema | `source_backed` asset without lineage. | Validation fails. |
| Semantic schema | `needs_review` asset without lineage. | Validation succeeds. |
| Markdown | Generate Markdown from semantic JSON. | Required YAML frontmatter and source lineage section are present. |

## Backend Integration Tests

| Flow | Test Case | Expected Result |
| --- | --- | --- |
| Health | `GET /health`. | Returns `{"status": "ok"}`. |
| Upload | `POST /documents/upload` with a text file. | Returns document version metadata with SHA-256 and `STORED`. |
| Catalog | `GET /documents` after upload. | Uploaded document appears in catalog. |
| Detail | `GET /documents/{document_id}`. | Version metadata is returned. |
| Extraction | `POST /documents/{document_id}/versions/{version_id}/extract`. | Raw extraction JSON contains parser metadata and source references. |
| Extraction retrieval | `GET /documents/{document_id}/versions/{version_id}/extraction` before extraction. | API returns `404`. |
| Extraction retrieval | `GET /documents/{document_id}/versions/{version_id}/extraction` after extraction. | Cached raw extraction JSON is returned. |
| Semantic | `POST /documents/{document_id}/versions/{version_id}/semantic`. | Semantic JSON and Markdown are returned with `needs_review`. |
| Semantic retrieval | `GET /documents/{document_id}/versions/{version_id}/semantic` before generation. | API returns `404`. |
| Semantic retrieval | Repeated `POST`/`GET` semantic calls. | Cached semantic output is returned without regenerating a new semantic id. |
| Markdown retrieval | `GET /documents/{document_id}/versions/{version_id}/markdown` before generation. | API returns `404`. |
| Markdown retrieval | `GET /documents/{document_id}/versions/{version_id}/markdown` after generation. | Cached Markdown is returned as `text/markdown`. |
| Empty upload | Upload empty file. | API returns `400` with explicit error. |
| Duplicate extraction | Extract duplicate version. | API returns `409` and explains duplicate versions are not extracted independently. |
| Persistent app | Recreate app with the same `data_dir`. | Catalog entries remain available. |

## Frontend Unit Tests

These tests are written as skipped placeholders until `apps/web` is scaffolded.

| Component | Test Case | Expected Result |
| --- | --- | --- |
| Upload form | No file selected. | Upload action is disabled or validation error is shown. |
| Upload form | File selected. | Filename, type, and size are displayed before submit. |
| Catalog row | Document has duplicate status. | Duplicate/version indicator is visible. |
| Status badge | Extraction failed. | Failed state is visually distinct and error details are available. |
| Markdown preview | Semantic output has warnings. | Warnings remain visible and are not hidden behind success UI. |
| Review panel | No semantic output exists. | Validate/reject actions are disabled. |
| Review panel | Semantic output exists. | Validate/reject actions are available. |

## Frontend Integration Tests

These tests are represented in `apps/web/e2e/document-ingestion.spec.ts` and should be enabled once the Next.js app exists.

| Flow | Test Case | Expected Result |
| --- | --- | --- |
| Upload to catalog | Upload a text file. | Catalog shows filename, hash, and lifecycle status. |
| Duplicate upload | Upload identical bytes under another filename. | UI shows duplicate/version indicator. |
| Extraction review | Trigger extraction and open Markdown preview. | Markdown, `needs_review`, warnings, and source lineage are visible. |
| Failed extraction | Backend returns failure. | UI shows understandable failure state and does not show successful review actions. |
| Validation | User validates semantic output. | Status changes to validated. |
| Rejection | User rejects semantic output. | Status changes to rejected and notes can be recorded. |
