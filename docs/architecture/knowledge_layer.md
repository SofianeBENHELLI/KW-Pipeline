# Knowledge Layer Architecture

This document describes the knowledge layer that sits on top of the
KW Pipeline document review pipeline. The layer is additive: it
consumes already-`VALIDATED` `SemanticDocument`s and produces a
queryable knowledge graph plus a chat surface. It does not modify
the existing parser, semantic-extraction, review, or persistence
paths.

Companion ADRs:

- [ADR-012](../adr/ADR-012-knowledge-graph-layer.md) — gate
  placement, graph store choice, what to vendor.
- [ADR-013](../adr/ADR-013-llm-provider-and-no-langchain.md) —
  Anthropic Claude only, no LangChain, why.

## High-level shape

```
                     ┌─────────────────────────────────────┐
                     │       KW Pipeline core              │
   upload ──▶ parse ─▶ extract ─▶ semantic ─▶ NEEDS_REVIEW │
                     │                            │        │
                     │                  ┌─────────▼──────┐ │
                     │                  │ human reviewer │ │
                     │                  └─────────┬──────┘ │
                     │                            │        │
                     │            ┌──── REJECTED  │        │
                     │            │               │        │
                     │     ┌──────▼─────┐  ┌──────▼──────┐ │
                     │     │ rejected   │  │ VALIDATED   │ │
                     │     └────────────┘  └──────┬──────┘ │
                     └────────────────────────────┼────────┘
                                                  │ side-effect
                                                  ▼
                                          ┌──────────────┐
                                          │ KNOWLEDGE    │
                                          │ LAYER        │
                                          ├──────────────┤
                                          │ • projector  │
                                          │ • extractor  │
                                          │ • graph      │
                                          │ • chat       │
                                          └──────────────┘
                                                  │
                                          ┌───────┴─────────┐
                                          ▼                 ▼
                                    Neo4j graph        Anthropic SDK
                                    (5.x Community)    (Claude)
```

The dashed box on the right is the new layer. Everything outside
it stays exactly as it is today.

## Module layout

New modules added under `apps/api/app/services/knowledge/`:

| Module | Purpose | Phase |
|---|---|---|
| `graph_store.py` | `GraphStore` Protocol + `Neo4jGraphStore` impl + `InMemoryGraphStore` test fake | 1 |
| `projector.py` | Projects a `SemanticDocument` into the graph: `(:Document)`, `(:Version)`, `(:Section)` nodes + `PART_OF` edges | 1 |
| `entity_extractor.py` | LLM-driven entity/relation extraction over validated section text; emits `(:Entity)` + `HAS_ENTITY` edges with citations | 2 |
| `llm_client.py` | `LLMClient` Protocol + `AnthropicLLMClient` impl + `FakeLLMClient` test fake | 2 |
| `chat_service.py` | RAG / GraphRAG / Vector chat dispatcher; calls `GraphStore` and `LLMClient` | 3 |

New schemas under `apps/api/app/schemas/`:

- `knowledge.py` — Pydantic models for nodes, edges, triples, chat
  requests/responses. Inherits from `APISchemaModel` (introduced in
  PR #107 for #80) and carries an explicit `schema_version` literal
  per ADR-008.

New routes added in `apps/api/app/routes.py`:

| Route | Phase | Operation ID | Returns |
|---|---|---|---|
| `GET /documents/{document_id}/graph` | 1 | `get_document_graph` | `KnowledgeGraphProjection` (nodes + edges for one document) |
| `GET /knowledge/graph` | 1 | `get_knowledge_graph` | Cursor-paginated `KnowledgeGraphPage` (cross-document subgraph) |
| `POST /chat/rag` | 3 | `chat_rag` | `ChatResponse` (vector retrieval over validated content) |
| `POST /chat/graph` | 3 | `chat_graph` | `ChatResponse` (Cypher-translated query over the graph) |
| `POST /chat/hybrid` | 3 | `chat_hybrid` | `ChatResponse` (vector + graph blended) |

Frontend additions under `apps/web/src/features/`:

| Slice | Component | Phase |
|---|---|---|
| `graph/` | `<KnowledgeGraphView />` (wraps `@neo4j-nvl/react`) | 1 |
| `chat/` | `<ChatPanel />`, `<ChatModeToggle />` (RAG / Graph / Hybrid) | 3 |

## Gate placement and the FSM

The lifecycle FSM in [`apps/api/app/models/document.py`](../../apps/api/app/models/document.py)
is **unchanged**. No new states. No new transitions.

Graph projection is wired as a side-effect of the existing
`mark_validated` call in `DocumentService`. The pseudo-code:

```python
def mark_validated(self, *, document_id, version_id, reviewer_note):
    self._catalog.update_version_status(...)              # existing
    self._semantic_outputs.record_validation(...)         # existing
    try:
        self._knowledge.project(document_id, version_id)  # new, fire-and-log
    except Exception:
        log.exception("graph projection failed; document still validated")
```

Implications:

- **Validation never fails because of a graph error.** A Neo4j outage
  or an LLM hiccup leaves the SQLite catalog correct; the graph
  catches up later via a retry loop or a manual reproject.
- **No reverse migration on the catalog.** If `KnowledgeService` is
  not configured (no Neo4j connection string, no Anthropic key), the
  call is a no-op and the existing pipeline behaves identically.
- **Phase 2 entity extraction follows the same pattern.** It is a
  side-effect of validation, runs after projection, and emits its
  own log line on failure. The graph reflects whatever the LLM
  produced *and* what passed Pydantic validation *and* what carried
  a valid source reference. Anything else is dropped with a warning.

## Audit guarantees carried into the graph

- **Every `(:Section)` node** carries the originating
  `document_version_id` and the `section_id` from `SemanticDocument`.
- **Every `(:Entity)` node** is reachable from at least one
  `(:Section)` via `HAS_ENTITY`. Orphans are pruned at write time.
- **Every `HAS_ENTITY` edge** carries a `source_reference_id` —
  pointing at a row in the existing `source_references` table — so
  Orbital can deep-link from a graph entity back to the exact line
  span in the original document.
- **No edge without a citation.** Triples lacking a
  `source_reference_id` are dropped at the boundary in
  `entity_extractor.py`. This is the equivalent of ADR-009's
  `review_status="needs_review"` policy applied to graph edges.
- **Schema version on every payload.** Migrations follow the
  ADR-008 pattern.

## Configuration surface

The knowledge layer is opt-in via environment variables. All env-var
reads — both knowledge-layer settings and the older upload / CORS
guardrails — flow through `app.settings.Settings` (#43, a thin
`pydantic_settings.BaseSettings` subclass). The `KW_` prefix is the
canonical name; legacy unprefixed names are accepted as
`pydantic.AliasChoices` so existing deployments keep working without a
config rewrite.

| Env var (canonical) | Legacy alias | Purpose | Default |
|---|---|---|---|
| `KW_NEO4J_URI` | — | `bolt://...` connection string | unset (layer disabled) |
| `KW_NEO4J_USER` | — | Auth username | unset |
| `KW_NEO4J_PASSWORD` | — | Auth password | unset |
| `KW_NEO4J_DATABASE` | — | Neo4j database name | `neo4j` |
| `KW_ANTHROPIC_API_KEY` | `ANTHROPIC_API_KEY` | LLM access | unset (Phases 2+ disabled) |
| `KW_ANTHROPIC_MODEL` | `KW_LLM_MODEL` | Claude model id | SDK default (`claude-sonnet-4-5`) |
| `KW_KNOWLEDGE_LAYER_ENABLED` | — | Master kill-switch | `false` |
| `KW_MAX_UPLOAD_BYTES` | `MAX_UPLOAD_BYTES` | Upload byte ceiling | `52428800` (50 MiB) |
| `KW_ALLOWED_CONTENT_TYPES` | `ALLOWED_CONTENT_TYPES` | MIME allowlist (CSV) | `text/plain` |
| `KW_CORS_ALLOWED_ORIGINS` | `CORS_ALLOWED_ORIGINS` | CORS allowlist (CSV) | empty (no cross-origin) |

When `KW_KNOWLEDGE_LAYER_ENABLED=false`, all five graph + chat
endpoints return `503 Service Unavailable` with a `detail` explaining
the layer is disabled. The existing pipeline's behavior is identical
to today.

## Test layout

Unit tests use the in-memory fakes; no Docker required:

- `tests/test_graph_projector.py` — given a `SemanticDocument`, the
  projector calls the expected `GraphStore` operations (Phase 1).
- `tests/test_entity_extractor.py` — given a recorded `LLMClient`
  response, the extractor produces validated triples; missing-source
  triples are dropped (Phase 2).
- `tests/test_chat_service.py` — mode dispatch, source attribution
  payload shape (Phase 3).

Integration tests against a real Neo4j run behind
`pytest -m integration`, opt-in:

- `tests/integration/test_neo4j_graph_store.py` — real `bolt://`
  connection; CRUD round-trip on a temporary database.

LLM integration tests against the real Anthropic API run behind
`pytest -m llm_integration`, opt-in and not part of default CI:

- `tests/integration/test_anthropic_llm_client.py` — happy-path
  smoke; rate-limit handling.

## Docker / local dev

`docker/docker-compose.yml` (added in Phase 1b) defines:

- `neo4j` — Neo4j 5.23 Community, ports 7474 (browser) + 7687 (bolt),
  named volume `neo4j_data`, healthcheck via `wget` against the
  HTTP port.
- `api` — the FastAPI app (built from `apps/api/Dockerfile`) with
  `KW_KNOWLEDGE_LAYER_ENABLED=true` and `KW_NEO4J_*` env vars wired
  to the neo4j service. `depends_on: neo4j (service_healthy)`.

There is intentionally no `web`/frontend service; the Vite dev server
runs on the host (`npm run dev` from `apps/web/`).

```sh
# Just Neo4j (run the API on the host with `uvicorn`):
docker compose -f docker/docker-compose.yml up -d neo4j

# Full backend stack for end-to-end demos:
docker compose -f docker/docker-compose.yml up

# Run the integration tests once Neo4j is healthy:
cd apps/api && \
  KW_NEO4J_URI=bolt://localhost:7687 \
  KW_NEO4J_USER=neo4j \
  KW_NEO4J_PASSWORD=test_password_change_me \
  pytest -m integration --override-ini="addopts=-ra --strict-markers --strict-config -m integration"
```

## Phase status

- **Phase 1 — graph projection.** Shipped. `KnowledgeProjector` materialises every `(:Document)`, `(:Version)`, `(:Section)`, `(:Chunk)`, `(:Topic)`, and structural edge from the validated `SemanticDocument`.
- **Phase 2 — LLM entity extraction with citation enforcement.** **Closed 2026-05-04.** All four ADR-014 acceptance items are on `main`: section-level prompt with tool-use, two-gate citation enforcement, prompt-injection sanitization, ephemeral prompt caching, exponential-backoff retry on 429/5xx (§4), and per-document `input_tokens` circuit breaker (§3, default off — opt-in via `KW_ENTITY_EXTRACTOR_MAX_INPUT_TOKENS_PER_DOCUMENT`). Residual deferred follow-up: section batching to amortise cache hits ([#195](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/195)).
- **Phase 3 — vector RAG.** Embedding-client scaffold (`EmbeddingClient` Protocol + `VoyageEmbeddingClient` + `FakeEmbeddingClient`) and the `voyage_api_key` / `embedding_model` settings live on `main` per ADR-015. Implementation (Neo4j HNSW vector index + chunk-embedding write path + `GET /knowledge/search`) is tracked in [#186](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/186) and not yet wired into the projector or any route.
- **Reconciliation surface.** A reconciliation service exists (`apps/api/app/services/knowledge/reconciliation.py`); the operator-facing route / CLI is tracked in [#124](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/124).

## Out-of-scope for this architecture doc

- **Embedding model and vector index implementation details.** Tracked in [#186](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/186); see ADR-015 for the provider commitment.
- **Cypher-generation prompt design.** Phase 3 ADR (chat surface).
- **Prompt-caching policy and cost telemetry.** ADR-014.
- **3DEXPERIENCE widget composition** of graph + chat. #78.
- **Multi-tenant data isolation** for the graph. #91.
- **Sensitive data detection** before publishing to the graph. #92.
