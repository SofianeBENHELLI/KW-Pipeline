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

The knowledge layer is opt-in via environment variables. Until
Pydantic Settings (#43) lands, these are read with the same
`os.environ.get` pattern the rest of the codebase uses.

| Env var | Purpose | Default |
|---|---|---|
| `KW_NEO4J_URI` | `bolt://...` connection string | unset (layer disabled) |
| `KW_NEO4J_USER` | Auth username | unset |
| `KW_NEO4J_PASSWORD` | Auth password | unset |
| `KW_NEO4J_DATABASE` | Neo4j database name | `neo4j` |
| `ANTHROPIC_API_KEY` | LLM access | unset (Phases 2+ disabled) |
| `KW_LLM_MODEL` | Claude model id | `claude-sonnet-4-5` |
| `KW_KNOWLEDGE_LAYER_ENABLED` | Master kill-switch | `false` |

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

A new `docker/docker-compose.yml` lands in Phase 1. It defines:

- `neo4j` — Neo4j Community 5.x, ports 7474 (browser) + 7687 (bolt).
- `api` — the FastAPI app with `KW_NEO4J_*` env vars wired to the
  neo4j service.

Running just `docker compose up neo4j` is enough to develop the
knowledge layer locally; the API can run on the host as before.
The full `docker compose up` is for end-to-end demos.

## Out-of-scope for this architecture doc

- **Embedding model and vector index.** Phase 3 ADR.
- **Cypher-generation prompt design.** Phase 3 ADR.
- **Prompt-caching policy and cost telemetry.** Phase 2 ADR.
- **3DEXPERIENCE widget composition** of graph + chat. #78.
- **Multi-tenant data isolation** for the graph. #91.
- **Sensitive data detection** before publishing to the graph. #92.
