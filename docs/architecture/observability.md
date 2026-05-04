# Observability — structured event vocabulary

This document is the canonical reference for the lifecycle events
the KW-Pipeline backend emits. Implementations live in
[`apps/api/app/services/`](../../apps/api/app/services/) and the
audit-trail tests live in
[`apps/api/tests/test_observability.py`](../../apps/api/tests/test_observability.py).

The structured-logging plumbing — JSON renderer, formatter, output
selector — is in [`apps/api/app/logging_config.py`](../../apps/api/app/logging_config.py)
and is set by `KW_LOG_FORMAT=json|text` (issue #42).

## Conventions

- Every event has a stable, dotted name (`document.uploaded`,
  `extraction.failed`, …). The name is the log message —
  `log.info("event.name", extra={...})` — so it survives unchanged
  whether the formatter is the `json` or `text` shape.
- Every event with a document scope carries `document_id` and
  `version_id` keys in `extra`. Joining records on those two values
  reconstructs the per-version timeline.
- Numeric metrics use `bytes` / `bytes_in` for sizes and
  `section_count` / `sections_out` for counts. Identifiers use
  `parser_name`, `content_type`, etc. as strings.
- **Logs never carry raw file bytes or full extracted text.** A
  `failure_reason` is a short human-readable string; an exception
  type is logged separately in `exc_info`. The on-call greppers
  rely on this; the
  [`test_observability.py`](../../apps/api/tests/test_observability.py)
  suite asserts it.

## Event vocabulary

### Document lifecycle

| Event | Level | Where emitted | Key `extra` fields |
|---|---|---|---|
| `document.uploaded` | INFO | `DocumentService.upload` (and `replace`) | `document_id`, `version_id`, `version_number`, `sha256`, `bytes`, `content_type`, `document_filename`, `is_duplicate` |
| `document.status_changed` | INFO | `DocumentService` after every FSM transition | `document_id`, `version_id`, `from`, `to` |

`document.uploaded` is fired exactly once per upload, including
duplicates — `is_duplicate=true` flags the deduped path. `sha256`
is the canonical join key between an original and its duplicate
re-uploads.

### Extraction

| Event | Level | Where emitted | Key `extra` fields |
|---|---|---|---|
| `extraction.started` | INFO | `ExtractionJobService.run` start | `document_id`, `version_id`, `content_type`, `bytes_in` |
| `extraction.succeeded` | INFO | `ExtractionJobService.run` success | `document_id`, `version_id`, `parser_name`, `bytes_in`, `sections_out` |
| `extraction.failed` | WARNING | `ExtractionJobService.run` failure (parser missing, parser raised, no extractable content) | `document_id`, `version_id`, `parser_name` (or `null` when no parser was found), `failure_reason` |
| `extraction.retried` | INFO | `ExtractionJobService.retry_extract` (#87), fired *before* the new attempt runs | `document_id`, `version_id`, `previous_failure_reason` |

`parser_name` is the parser's declared `name` attribute
(e.g. `"plain_text"`, `"docx"`, `"pdf"`, `"pptx"`). It matches
`RawExtraction.parser_name`, so logs and stored extractions can be
joined on a single value.

### Semantic projection

| Event | Level | Where emitted | Key `extra` fields |
|---|---|---|---|
| `semantic.generated` | INFO | `SemanticOutputService.materialize` after a fresh build | `document_id`, `version_id`, `section_count` |
| `semantic.cached` | INFO | `SemanticOutputService.materialize` when an existing artifact is returned | `document_id`, `version_id`, `section_count` |

### Review

| Event | Level | Where emitted | Key `extra` fields |
|---|---|---|---|
| `review.validated` | INFO | `DocumentService.validate` | `document_id`, `version_id`, `reviewer_note` (when present) |
| `review.rejected` | INFO | `DocumentService.reject` | `document_id`, `version_id`, `reviewer_note` (when present) |

### Knowledge layer (optional)

These only fire when `KW_KNOWLEDGE_LAYER_ENABLED=true`. See
[ADR-012](../adr/ADR-012-knowledge-graph-layer.md) and
[ADR-013](../adr/ADR-013-llm-provider-and-no-langchain.md) for the
gating rules.

| Event | Level | Where emitted | Key `extra` fields |
|---|---|---|---|
| `knowledge.projection.written` | INFO | `KnowledgeProjector` after a successful projection | `document_id`, `version_id`, node and edge counts |
| `knowledge.entity_extraction.completed` | INFO | Phase 2 entity extractor finishes | `document_id`, `version_id`, `entity_count`, token usage |
| `knowledge.embeddings.computed` | INFO | Phase 3: `KnowledgeProjector` after writing chunk embeddings | `document_id`, `version_id`, `chunk_count`, `embedding_model`, `cache_hits`, `embedded_count` |
| `knowledge.embeddings.failed` | WARNING | Phase 3: embedding write fails (fire-and-log; structural projection is unaffected) | `document_id`, `version_id`, `error_type` |
| `knowledge.vector_index.created` | INFO | Phase 3: `app.main` startup successfully provisioned the chunk-embedding HNSW index | `index_name`, `dim`, `embedding_model`, `store` |
| `knowledge.vector_index.failed` | WARNING | Phase 3: startup index provisioning raised (e.g. Neo4j blip); the API still serves Phase 1 + Phase 2 traffic | `index_name`, `embedding_model`, `error_type` |
| `knowledge.search.queried` | INFO | Phase 3: every `GET /knowledge/search` request | `query_char_count`, `top_k`, `result_count`, `embedding_model`, `latency_ms` |
| `knowledge.chat.answered` | INFO | Phase 3 chat: every `POST /knowledge/chat` request | `mode`, `top_k`, `vector_hits`, `graph_triples`, `embedding_model`, `llm_model`, `input_tokens`, `output_tokens`, `latency_ms` |

### Idempotency

| Event | Level | Where emitted | Key `extra` fields |
|---|---|---|---|
| `idempotency.replayed` | INFO | Route layer when a request matches a stored idempotency key | `route`, `idempotency_key` |

## How to grep this in practice

Set the JSON formatter for production deployments:

```bash
export KW_LOG_FORMAT=json
```

Then standard JSON-aware tools work:

```bash
# Every state transition for one version:
journalctl -u kw-pipeline --output=cat \
  | jq 'select(.version_id == "abc-123")'

# All deduped uploads in the last hour:
... | jq 'select(.event == "document.uploaded" and .is_duplicate == true)'

# Parser failures by parser:
... | jq 'select(.event == "extraction.failed") | .parser_name' \
  | sort | uniq -c | sort -rn

# Slowest extractions (joined with extraction.started):
# Pair `extraction.started` and `extraction.succeeded` records on
# `version_id`, subtract timestamps. The `version_id` is unique per
# version, so this is a 1:1 join.
```

For local development, leave `KW_LOG_FORMAT=text` (the default) and
the records render as `INFO app.services.document_service document.uploaded`
plus a stdlib-formatted message.

## Adding a new event

1. Pick a stable dotted name. Verb in past tense for done events
   (`document.uploaded`, `extraction.succeeded`); present
   continuous for in-progress (`extraction.started`).
2. Always include `document_id` and `version_id` when the event has
   a document scope.
3. Never include raw bytes, full extracted text, or PII. A
   short safe message is fine; if you need the full content, it
   already lives in the catalog or extraction record — log a
   correlation ID and let the reader fetch it.
4. Add an entry to this document under the right lifecycle section.
5. Add an assertion in `tests/test_observability.py` that pins the
   event name and the canonical `extra` keys.
