# Logging and audit trail

> Status: Implemented in #42. Default shape is `text` for local dev;
> production containers flip `KW_LOG_FORMAT=json` to emit one JSON
> object per log line.

The Harvester API funnels every significant lifecycle moment through
the standard library's `logging` module using a consistent
`event_name + extra={...}` shape. A reviewer or on-call engineer can
recover the complete history of a document version by grepping
production logs for that version's UUID.

## Configuration

Two environment variables drive the log surface. Both flow through
`app.settings.Settings`:

| Setting          | Env var          | Default | Notes                                    |
| ---------------- | ---------------- | ------- | ---------------------------------------- |
| `log_format`     | `KW_LOG_FORMAT`  | `text`  | `text` or `json`. Flip to `json` in prod |
| `log_level`      | `KW_LOG_LEVEL`   | `INFO`  | Standard Python level names              |

`app.main.create_app` calls `app.logging_config.configure_logging(...)`
once per app instance. The function replaces the root handler, so it
is safe to call repeatedly across `TestClient` instances.

## JSON shape

Every line in JSON mode is a single object on stdout:

```json
{
  "timestamp": "2026-05-01T08:30:21.412Z",
  "level": "INFO",
  "logger": "app.services.document_service",
  "event": "document.uploaded",
  "document_id": "9a4f…",
  "version_id": "6b22…",
  "version_number": 1,
  "sha256": "0a31…",
  "bytes": 1842,
  "content_type": "text/plain",
  "filename": "policy.txt",
  "is_duplicate": false
}
```

`event` is always the string passed to `log.info()`. Every keyword
argument passed via `extra={...}` is merged at the top level. Reserved
stdlib `LogRecord` attributes (filename, funcName, …) are never
emitted.

## Event catalogue

All events listed below are emitted at `INFO` unless noted otherwise.

### Document lifecycle (`app.services.document_service`)

| Event                       | Trigger                                       | Keys                                                                                                          |
| --------------------------- | --------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `document.uploaded`         | A `DocumentVersion` is persisted              | `document_id`, `version_id`, `version_number`, `sha256`, `bytes`, `content_type`, `filename`, `is_duplicate`  |
| `document.status_changed`   | Any FSM move on a version                     | `document_id`, `version_id`, `from`, `to`                                                                     |
| `review.validated`          | Reviewer accepts                              | `document_id`, `version_id`, `reviewer_note`                                                                  |
| `review.rejected`           | Reviewer rejects                              | `document_id`, `version_id`, `reviewer_note`                                                                  |

### Extraction (`app.services.extraction_job_service`)

| Event                  | Level   | Keys                                                                                       |
| ---------------------- | ------- | ------------------------------------------------------------------------------------------ |
| `extraction.started`   | INFO    | `document_id`, `version_id`, `content_type`, `bytes_in`                                    |
| `extraction.succeeded` | INFO    | `document_id`, `version_id`, `parser_name`, `bytes_in`, `sections_out`                     |
| `extraction.failed`    | WARNING | `document_id`, `version_id`, `parser_name` (may be `null`), `failure_reason`               |

### Semantic generation (`app.services.semantic_output_service`)

| Event                | Keys                                                |
| -------------------- | --------------------------------------------------- |
| `semantic.generated` | `document_id`, `version_id`, `section_count`        |
| `semantic.cached`    | `document_id`, `version_id`, `section_count`        |

### Knowledge layer (`app.services.knowledge.projector`, `app.routes`)

| Event                                       | Level   | Keys                                                                                                                                          |
| ------------------------------------------- | ------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `knowledge.projection.written`              | INFO    | `document_id`, `version_id`, `store`, `node_count`, `edge_count`                                                                              |
| `knowledge.projection.failed`               | ERROR   | `document_id`, `version_id` (plus stack trace via `exc_info`)                                                                                 |
| `knowledge.entity_projection.written`       | INFO    | `document_id`, `version_id`, `store`, `entity_node_count`, `has_entity_edge_count`, `warning_count`, `token_usage`                            |
| `knowledge.entity_projection.uncited_triple_skipped` | WARNING | `document_id`, `version_id`, `subject`                                                                                                |
| `knowledge.entity_extraction.completed`     | INFO    | `document_id`, `version_id`, `triple_count`, `warning_count`, `token_usage`                                                                   |
| `knowledge.entity_extraction.failed`        | ERROR   | `document_id`, `version_id` (plus stack trace via `exc_info`)                                                                                 |

### HTTP-level audit (`app.routes`)

| Event                  | Keys                                              |
| ---------------------- | ------------------------------------------------- |
| `idempotency.replayed` | `route`, `idempotency_key`, `response_status`     |

> Per-request access logs are out of scope for #42; uvicorn's
> `uvicorn.access` logger is already running and covers that surface.

## Recovering a version's narrative

In a JSON deployment, grepping for the version UUID returns the
ordered audit trail (timestamps prefix every line). Example:

```sh
grep '"version_id":"6b22…"' /var/log/harvester.log | jq -r '.event'
```

Expected sequence for a happy-path validation:

```
document.uploaded
extraction.started
document.status_changed   # STORED → EXTRACTING
document.status_changed   # EXTRACTING → EXTRACTED
extraction.succeeded
semantic.generated
document.status_changed   # EXTRACTED → NEEDS_REVIEW
document.status_changed   # NEEDS_REVIEW → VALIDATED
review.validated
knowledge.projection.written
knowledge.entity_extraction.completed   # if Phase 2 is enabled
```

A failed extraction follows the prefix `document.uploaded →
extraction.started → extraction.failed → document.status_changed
(EXTRACTING → FAILED)`.
