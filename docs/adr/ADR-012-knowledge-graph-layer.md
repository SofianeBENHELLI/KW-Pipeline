# ADR-012: Knowledge Graph Layer Sits Behind the Review Gate

## Status

Accepted, 2026-05-01.

## Context

The KW Pipeline roadmap calls for a knowledge layer on top of the document
review pipeline: typed entity/relation extraction (#48), knowledge taxonomy
and export (#22, #23), and eventually a chat surface that lets reviewers
ask questions across the validated corpus. We surveyed
[neo4j-labs/llm-graph-builder](https://github.com/neo4j-labs/llm-graph-builder)
(Apache-2.0) as a reference design — it solves the adjacent shape:
documents → LLM-extracted graph in Neo4j → multi-mode (RAG / GraphRAG /
Vector) chat.

That repo is a useful pattern source but the wrong wholesale fit. KW
Pipeline's identity is the **review gate** (ADR-009) and **source
lineage** (`SourceReference` carries `(document_version_id, section_id,
page_number, line_start, line_end)` for every claim). A graph
populated by an LLM bypassing those guardrails would dilute the audit
model that the rest of the system is built around.

This ADR commits to a layered architecture: a new "knowledge layer"
that consumes already-`VALIDATED` `SemanticDocument`s and produces a
graph + chat surface. Phase 0 captures the four decisions that
constrain everything downstream so the implementation can proceed
without re-litigating them per phase.

## Decision

### 1. Graph store: Neo4j Community via Docker, behind a Protocol

A `GraphStore` Protocol in `app.services.knowledge.graph_store` defines
the operations the rest of the codebase is allowed to perform on the
graph (`upsert_document_node`, `upsert_section_node`,
`merge_part_of_edge`, `find_neighbors`, `cypher_query` — final list
sized in Phase 1). The first concrete implementation is `Neo4jGraphStore`
backed by Neo4j Community 5.x via the official `neo4j` Python driver.

Rationale:

- Neo4j is the most direct path to the patterns we want to vendor from
  llm-graph-builder (Cypher MERGE shapes, deadlock retry logic, graph
  visualization via `@neo4j-nvl/react`). Other options (Kuzu, Apache
  AGE on Postgres, DuckDB-PGQ) are interesting but have smaller
  ecosystems and would require more bespoke patterns.
- Neo4j Community is permissively licensed (GPLv3 for the server,
  Apache-2.0 for the driver) and runs in a single Docker container —
  fits the local-first demo path.
- Hiding it behind a Protocol means a future swap is a service-layer
  change, not a system rewrite. Tests use an in-memory fake; no
  Neo4j is required to run the unit suite.

Rejected alternatives:

- **Apache AGE on Postgres**: would let us reuse SQLite-style
  persistence patterns, but Cypher-on-Postgres is a thin layer with a
  smaller ecosystem and no equivalent of `@neo4j-nvl/react`.
- **Kuzu (embedded)**: tempting for the no-Docker path, but the Cypher
  dialect lags Neo4j's and the visualization story is weaker.
- **Roll our own with SQLite + adjacency tables**: would preserve the
  zero-Docker dev story but means we re-invent every graph operation
  (path queries, subgraph extraction) from scratch.

### 2. LLM provider: Anthropic Claude via the official SDK; no LangChain

Phase 2 (entity extraction) and Phase 3 (chat) need an LLM. We commit
to a single provider for v1 — Anthropic Claude via the `anthropic`
Python SDK — wrapped behind an `LLMClient` Protocol so future providers
slot in without touching call sites. ADR-013 captures the
"no LangChain" decision in detail.

The pattern is the same as the `GraphStore` decision: one concrete
implementation, hidden behind a Protocol, swappable later.

### 3. Implementation: vendor patterns, not packages

Five things from llm-graph-builder are worth borrowing as **patterns**
(not as a dependency or a wholesale copy):

| Pattern | Source file (llm-graph-builder) | Target file (KW Pipeline) |
|---|---|---|
| Cypher MERGE shapes for `(:Document)`, `(:Section)`, `(:Entity)`, `PART_OF`, `HAS_ENTITY` | `backend/src/make_relationships.py` | `app/services/knowledge/graph_store.py` |
| Deadlock-retry wrapper for write transactions | `backend/src/graphDB_dataAccess.py` | same file |
| Structured-output prompt + Pydantic validation for entity/relation triples | `backend/src/llm.py` (`LLMGraphTransformer`) | `app/services/knowledge/entity_extractor.py` (Phase 2) |
| Multi-mode chat (RAG / GraphRAG / Vector) endpoint taxonomy | `backend/src/QA_integration.py` | `app/services/knowledge/chat_service.py` (Phase 3) |
| Graph visualization | `@neo4j-nvl/react` library | `apps/web/src/features/graph/` (Phase 1) |

Only the last item is taken as a dependency. Everything else is a
read-and-reimplement pattern source. Total expected vendored LOC: ~300
backend + a thin React wrapper.

### 4. Gate placement: graph projection runs on `VALIDATED` documents only

The lifecycle FSM stays unchanged. Graph projection is a **side-effect
of the `VALIDATED` transition**, executed by `DocumentService.mark_validated`
after the catalog write succeeds. Failures are logged and retried out
of band; they do not roll back validation, and they do not block the
human reviewer.

Phase 2's LLM-driven entity extraction runs on the same trigger:
validated section text → triples → graph. Triples that fail Pydantic
validation are dropped with a warning. Triples without a backing
`source_reference_id` are dropped. Triples that pass land as
`(:Entity)` nodes plus `HAS_ENTITY` edges that carry the source
reference.

This preserves ADR-009's contract one level up: anything in the graph
was reviewed first, and every edge has a citation.

Rejected alternative: **eager extraction on `EXTRACTED`** would let us
populate the graph earlier, but it would put model output in the
graph before a human had seen it. The audit story matters more than
the latency story.

## Consequences

- **No changes to the existing FSM** ([apps/api/app/models/document.py](../../apps/api/app/models/document.py)).
  Graph projection is a side-effect of the existing
  `NEEDS_REVIEW → VALIDATED` transition. No new states, no new
  predecessors, no new terminal markers.
- **One new optional runtime dependency in Phase 1**: `neo4j` (the
  Python driver). It pulls a small native extension and no transitive
  ML or LLM SDKs. Skipping the Neo4j configuration leaves the new code
  paths inactive; `Neo4jGraphStore` is constructed lazily.
- **One new optional runtime dependency in Phase 2**: `anthropic`. Same
  story — opt-in via configuration; an `LLMClient` not configured means
  Phase 2 features simply don't run, and nothing else regresses.
- **Docker becomes part of the local-dev story for the full demo
  path.** A `docker/docker-compose.yml` lands in Phase 1 with Neo4j
  Community (and the API) so a contributor can `docker compose up` and
  see the full pipeline. Running just the API + tests, as today, stays
  Docker-free.
- **Tests stay deterministic.** The `GraphStore` Protocol gets an
  in-memory fake (`InMemoryGraphStore`) used in unit tests; integration
  tests against a real Neo4j live behind a `pytest -m integration`
  marker, not part of the default suite. Same pattern for `LLMClient`:
  unit tests use a fake that returns recorded fixtures.
- **OpenAPI codegen continues to work for the new endpoints** (#80,
  PR #107). Every new knowledge route gets explicit `operation_id` +
  `response_model`, so the frontend's typed client is auto-generated as
  before.
- **Schema versioning policy applies.** New Pydantic models in
  `app/schemas/knowledge.py` carry an explicit `schema_version`
  literal (per ADR-008) and inherit from `APISchemaModel` (introduced
  in #80) so default-having list fields surface as required in the
  serialization-mode JSON Schema.
- **Frontend gains two new feature slices**: `apps/web/src/features/graph/`
  and `apps/web/src/features/chat/`. The existing review workspace
  ([`apps/web/src/features/review/`](../../apps/web/src/features/review/))
  remains the audit surface and is unaffected.
- **License posture unchanged.** llm-graph-builder is Apache-2.0;
  pattern reuse with attribution is permitted. The Neo4j driver and
  `@neo4j-nvl/react` are Apache-2.0. We don't ingest Neo4j Enterprise.

## What this ADR does not decide

- **Embedding model and vector index** for Phase 3's RAG mode. Deferred
  to a Phase 3-specific ADR; either Anthropic's hypothetical embedding
  endpoint, OpenAI text-embedding-3, or a local sentence-transformers
  model. Affects deployment footprint, not the gate placement.
- **Cypher generation prompt details and retry budget** for the
  GraphRAG chat mode. Deferred to Phase 3.
- **3DEXPERIENCE widget composition** of the new graph + chat surfaces.
  Tracked under #78; orthogonal to this ADR.
