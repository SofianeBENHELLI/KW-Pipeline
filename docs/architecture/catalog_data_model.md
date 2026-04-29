# Catalog Data Model

## Entities

### Document

Represents the logical document family.

| Field | Purpose |
| --- | --- |
| `id` | Stable document identifier. |
| `original_filename` | First uploaded filename for display. |
| `created_at` | First catalog creation timestamp. |
| `latest_version_id` | Pointer to the latest known version. |

### DocumentVersion

Represents one immutable binary upload.

| Field | Purpose |
| --- | --- |
| `id` | Stable version identifier. |
| `document_id` | Parent document identifier. |
| `version_number` | Monotonic version number within a document. |
| `filename` | Uploaded filename. |
| `content_type` | Uploaded content type. |
| `file_size` | Byte length. |
| `sha256` | Hash of immutable original bytes. |
| `storage_uri` | Raw file location. |
| `status` | Current lifecycle state. |
| `duplicate_of_version_id` | Existing version with the same hash, if any. |
| `created_at` | Version creation timestamp. |
| `failure_reason` | Explicit failure detail when status is `FAILED`. |

### IngestionRun

Tracks a single ingestion attempt.

| Field | Purpose |
| --- | --- |
| `id` | Stable run identifier. |
| `document_version_id` | Version being processed. |
| `started_at` | Start timestamp. |
| `finished_at` | End timestamp. |
| `status` | Run status. |
| `error_message` | Failure detail. |

### RawExtraction

Stores deterministic parser output.

| Field | Purpose |
| --- | --- |
| `id` | Stable extraction identifier. |
| `document_version_id` | Source version. |
| `parser_name` | Parser implementation. |
| `parser_version` | Parser version, when known. |
| `content` | Raw extraction JSON. |
| `created_at` | Creation timestamp. |

### SemanticDocument

Stores governed semantic output.

| Field | Purpose |
| --- | --- |
| `id` | Stable semantic document identifier. |
| `document_version_id` | Source version. |
| `schema_version` | Semantic contract version. |
| `validation_status` | `needs_review`, `validated`, or `rejected`. |
| `content` | Schema-validated semantic JSON. |
| `markdown_uri` | Generated Markdown location. |
| `warnings` | Review warnings. |
| `created_at` | Creation timestamp. |

### SourceReference

Connects semantic claims back to source material.

| Field | Purpose |
| --- | --- |
| `id` | Stable source reference identifier. |
| `document_version_id` | Source version. |
| `section_id` | Parser section or block identifier. |
| `page_number` | Page number, if available. |
| `line_start` | Start line, if available. |
| `line_end` | End line, if available. |
| `snippet` | Short source excerpt. |
