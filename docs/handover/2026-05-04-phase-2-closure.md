# Phase 2 closure — 2026-05-04

This handover marks **Phase 2 — LLM entity extraction with citation
enforcement — as closed**. It exists so future contributors can stop
asking "is Phase 2 done?" and start work on Phase 3 with a clean
anchor.

For the canonical repo snapshot, see
[2026-05-03-session.md](2026-05-03-session.md).

---

## What ships in this closure PR

| Track | Status | Where |
|---|---|---|
| ADR-014 §4 — exponential-backoff retry on 429/5xx | ✅ Shipped | `AnthropicLLMClient._call_with_retry` in [llm_client.py](../../apps/api/app/services/knowledge/llm_client.py); honours `Retry-After`, configurable via `max_retries`, default 1 |
| ADR-014 §3 — per-document `input_tokens` circuit breaker | ✅ Shipped | `EntityExtractor(max_input_tokens_per_document=...)` in [entity_extractor.py](../../apps/api/app/services/knowledge/entity_extractor.py); env var `KW_ENTITY_EXTRACTOR_MAX_INPUT_TOKENS_PER_DOCUMENT`, default `0` (disabled) |
| Widget catalog filter — server-side `?status=` + `?q=` | ✅ Shipped | [DocumentsList.tsx](../../apps/widget/src/sections/DocumentsList.tsx) and [client.ts](../../apps/widget/src/api/client.ts) — closes the cross-app story for #86 |
| Documentation reconciliation | ✅ Shipped | `docs/wiki/Roadmap.md`, `docs/roadmap/mvp_backlog_review.md`, `docs/architecture/knowledge_layer.md`, `docs/adr/ADR-014-entity-extraction-prompt-and-cost.md` |
| Section-batching follow-up | 📋 Tracked | [#195](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/195) — sole residual deferred item |

## Phase 2 — final state on `main`

| Capability | Where it lives |
|---|---|
| `LLMClient` Protocol + Anthropic implementation + ephemeral prompt caching | [llm_client.py](../../apps/api/app/services/knowledge/llm_client.py) |
| Two-gate citation enforcement (skip section without refs; drop triple citing unknown refs) | [entity_extractor.py:190-243](../../apps/api/app/services/knowledge/entity_extractor.py) |
| Defensive third gate at projection time | [projector.py:412-423](../../apps/api/app/services/knowledge/projector.py) |
| Prompt-injection sanitization (`### system:` etc.) | [entity_extractor.py:287-302](../../apps/api/app/services/knowledge/entity_extractor.py) |
| Rule-based enricher (date / monetary / requirement) | [rule_based_entities.py](../../apps/api/app/services/enrichers/rule_based_entities.py) |
| Backoff retry on 429/5xx | [llm_client.py](../../apps/api/app/services/knowledge/llm_client.py) — `_call_with_retry`, `_is_retryable`, `_retry_after_seconds` |
| Per-document token circuit breaker | [entity_extractor.py](../../apps/api/app/services/knowledge/entity_extractor.py) — `max_input_tokens_per_document` |
| Reconciliation service (programmatic) | [reconciliation.py](../../apps/api/app/services/knowledge/reconciliation.py); operator-facing route still tracked in [#124](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/124) |

## What is explicitly deferred (post-Phase-2)

| Topic | Tracked as | Why deferred |
|---|---|---|
| Section batching for the LLM extractor | [#195](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/195) | One-call-per-section is the simplest correct shape; batching is a measurable cost win, not a correctness gap. The constructor's `max_sections_per_call` knob is already reserved. |
| spaCy-backed person/organization NER | [#190](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/190) | Closes the rest of #48 (person/org enrichers); orthogonal to the LLM extractor and lives behind an opt-in `ner` extra. |
| Reconciliation HTTP / CLI surface | [#124](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/124) | Service layer exists; the operator-facing route is a small follow-up. |

## Phase 3 — kickoff brief ([#186](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/186))

Phase 3 = vector RAG (chunk embeddings + Neo4j HNSW + `GET /knowledge/search`).
ADR-015 is **Accepted** and the configuration scaffold landed in
[#178](https://github.com/SofianeBENHELLI/KW-Pipeline/pull/178):

- `EmbeddingClient` Protocol + `VoyageEmbeddingClient` + `FakeEmbeddingClient` in [embedding_client.py](../../apps/api/app/services/knowledge/embedding_client.py).
- `Settings.voyage_api_key` and `Settings.embedding_model` in [settings.py](../../apps/api/app/settings.py).
- `voyageai>=0.2` runtime dep, lazy-imported.
- `embedding_integration` pytest marker excluded from the default selector.

**Recommended PR1 of the Phase 3 sprint** — minimum slice that delivers a real `GET /knowledge/search` while keeping every existing test green and `VOYAGE_API_KEY` strictly optional:

1. **Vector index provisioning at startup.** Extend the `GraphStore` Protocol with `ensure_vector_index(*, name, dim)`; `Neo4jGraphStore` runs `CALL db.index.vector.createNodeIndex('chunk_embedding', 'Chunk', 'embedding', $dim, 'cosine')` (idempotent). `InMemoryGraphStore` is a no-op. Wire the call into `apps/api/app/main.py` startup, gated on `KW_KNOWLEDGE_LAYER_ENABLED=true` AND non-empty `voyage_api_key`.
2. **`KnowledgeSearchService`.** New `apps/api/app/services/knowledge/search.py` taking `EmbeddingClient + GraphStore`. Single `search(query, *, limit) -> list[ChunkSearchResult]`.
3. **`GET /knowledge/search` route.** Add to `apps/api/app/routes.py` next to the existing `/knowledge/graph`. Query params: `q` (1..200 chars), `limit` (1..50). Response model `ChunkSearchResponse` in `apps/api/app/schemas/knowledge.py`. 503 on Phase 3 disabled, 422 on empty query.
4. **Embedding write path.** Extend `KnowledgeProjector.project_chunks` to compute embeddings via `EmbeddingClient.embed_documents` and write `embedding: list[float]` onto the chunk node. Cache by `(model_id, sha256(text))`.
5. **Tests.** New `tests/test_knowledge_search.py` against `FakeEmbeddingClient` + an `InMemoryGraphStore` cosine shim. Real Voyage gated by `pytest -m embedding_integration`. Coverage stays ≥ 95%.
6. **Observability.** Emit `knowledge.embeddings.computed`, `knowledge.search.queried`, `knowledge.vector_index.created` per the table in #186.
7. **OpenAPI snapshot regen.** `python apps/api/scripts/export_openapi.py` to refresh `apps/api/openapi.json` and the regenerated `apps/web/src/api/schema.ts`.

## How to verify Phase 2 locally

```bash
# Default unit suite — fast, no API keys, no Neo4j, no Voyage.
cd apps/api
../../.venv312/bin/python -m pytest --cov=app --cov-fail-under=95 -q

# Real Anthropic happy-path smoke (needs ANTHROPIC_API_KEY in your .env).
../../.venv312/bin/python -m pytest -m llm_integration -q \
    --override-ini="addopts=-ra --strict-markers --strict-config -m llm_integration"
```

The retry path is exercised in unit tests via injected mock SDK
exceptions (no real network); the `llm_integration` smoke covers the
real SDK shape end-to-end.

## API keys (unchanged from 2026-05-03 handover)

- `ANTHROPIC_API_KEY` — Phase 2 entity extraction. [console.anthropic.com](https://console.anthropic.com)
- `VOYAGE_API_KEY` — Phase 3 vector embeddings (ADR-015 / required once #186 lands). [dash.voyageai.com](https://dash.voyageai.com)

---

*Generated 2026-05-04 by the Phase 2 closure session. The next dated
handover should land in `docs/handover/<date>-session.md` once Phase 3
PR1 ships.*
