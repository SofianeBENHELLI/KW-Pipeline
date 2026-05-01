# Knowledge Layer

The knowledge layer is an **opt-in** addition that turns validated documents into a queryable graph. It is dormant by default — with no env vars set, the existing pipeline behaves exactly as it did before.

## Why "opt-in" matters

The pipeline's audit story is non-negotiable: a reviewer signs off on each version, and the catalog is the source of truth. The knowledge layer is a *consumer* of the catalog, not a replacement for it. Specifically:

- **No graph projection before validation.** The projector runs as a fire-and-log side-effect of `mark_validated`. A reviewer sees a complete document with semantic JSON + Markdown before any node lands in Neo4j.
- **No edge without provenance.** Every `(:Entity)`-`HAS_ENTITY`-`(:Section)` relationship carries a `source_reference_id` that points at a row in the catalog's `source_references` table. Triples without source refs are dropped to `warnings`.
- **No rollback on graph failure.** A Neo4j outage or LLM hiccup does not revert validation. The catalog stays correct; the graph catches up via re-projection (issue #124 wires the explicit reconciliation surface).

ADR-012 captures the gate-placement decision; ADR-013 captures the no-LangChain stance.

## Two phases

### Phase 1 — graph projection (no LLM)

Active when `KW_KNOWLEDGE_LAYER_ENABLED=true`.

- `KnowledgeProjector.project(document, version, semantic)` takes a validated `SemanticDocument` and writes:
  - one `(:Document {id, kind="document", label})` node;
  - one `(:Version)` node per validated version;
  - one `(:Section)` node per `SemanticSection`;
  - `PART_OF` edges connecting them.
- Re-projection is safe — the version's prior subgraph is deleted before upserting the new one (`delete_subgraph_for_version`). Renamed or removed sections don't leave orphans.
- The `GraphStore` Protocol abstracts the backend. `InMemoryGraphStore` is the test fake; `Neo4jGraphStore` is the production impl. Cypher patterns and the deadlock-retry decorator are adapted from [`neo4j-labs/llm-graph-builder`](https://github.com/neo4j-labs/llm-graph-builder) (Apache-2.0).

### Phase 2 — LLM entity extraction

Active when `KW_KNOWLEDGE_LAYER_ENABLED=true` **and** `ANTHROPIC_API_KEY` is set.

- `EntityExtractor.extract(document, version, semantic)` calls Claude with a tool-use schema. The static system prompt earns `cache_control: {"type": "ephemeral"}` (ADR-014 §2 / Phase 2.1).
- The model emits typed `(subject, subject_type, predicate, object, object_type, confidence, source_section_id, source_reference_ids)` triples.
- **Triples without `source_reference_ids` are dropped to `warnings`.**
- **Triples whose `source_reference_ids` aren't a subset of the parent section's set are dropped.**
- Surviving triples produce `(:Entity)` nodes (id = stable hash of `(subject, subject_type)` so cross-document references converge) and `HAS_ENTITY` edges that carry the `source_reference_id` in `properties`.
- `LLMClient` is a Protocol with two impls: `FakeLLMClient` (used by all default unit tests; queue of recorded responses) and `AnthropicLLMClient`. A small `pytest -m llm_integration` job exercises the real Anthropic call but is opt-in and not part of default CI.

## Endpoints

| Route | Purpose |
|---|---|
| `GET /documents/{document_id}/graph` | Per-document subgraph; empty payload when no projection exists. |
| `GET /knowledge/graph?limit&cursor` | Cursor-paginated walk of the catalog-wide projection. |

## Frontend integration

`apps/web/src/features/graph/KnowledgeGraphView.tsx` wraps `@neo4j-nvl/react`. The component is **lazy-loaded** (PR #114) so the 600 KB-gz NVL runtime ships only when the graph panel mounts. Empty state renders when the document has no projection (knowledge layer disabled, or version not yet validated).

## Configuration

| Env var | Purpose | Default |
|---|---|---|
| `KW_KNOWLEDGE_LAYER_ENABLED` | Master kill-switch | unset → disabled |
| `KW_NEO4J_URI` | `bolt://...` connection string | unset → in-memory store |
| `KW_NEO4J_USER` / `KW_NEO4J_PASSWORD` / `KW_NEO4J_DATABASE` | Auth + DB name | unset / unset / `neo4j` |
| `ANTHROPIC_API_KEY` | Required for Phase 2 | unset → Phase 2 disabled |
| `KW_ANTHROPIC_MODEL` | Claude model id | `claude-sonnet-4-5` |

All of these flow through the `Settings(BaseSettings)` model (`apps/api/app/settings.py`). Legacy unprefixed names like `ANTHROPIC_API_KEY` keep working as Pydantic alias choices.

## What doesn't exist yet

- A **chat surface** (Phase 3). Mode taxonomy will mirror llm-graph-builder's `QA_integration.py` — RAG / GraphRAG / Hybrid — but reimplemented directly against the Anthropic SDK. ADR pending.
- A **vector index** for embeddings. Phase 3 ADR.
- A **reconciliation endpoint** to repair drift between the catalog and the graph (issue #124).
- **Multi-tenant isolation** in the graph (issue #91).

## Verification recipe

```bash
# 1. Bring up Neo4j alongside the API
docker compose -f docker/docker-compose.yml up -d neo4j

# 2. Tell the API to use it
export KW_KNOWLEDGE_LAYER_ENABLED=true
export KW_NEO4J_URI=bolt://localhost:7687
export KW_NEO4J_USER=neo4j
export KW_NEO4J_PASSWORD=test_password_change_me
export ANTHROPIC_API_KEY=sk-ant-...   # optional, enables Phase 2

# 3. Run the integration suite
cd apps/api && pytest -m integration -v
# -> 5 passing tests against the live Neo4j

# 4. (optional) the LLM-integration smoke test
pytest -m llm_integration -v
```

See [Operating Modes](Operating-Modes) for more.
