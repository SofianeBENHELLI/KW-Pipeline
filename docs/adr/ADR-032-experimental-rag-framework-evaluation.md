# ADR-032: Experimental Evaluation of LangChain and Microsoft GraphRAG

## Status

Proposed, 2026-05-17.

Amends: [ADR-013 — LLM Provider and No LangChain](ADR-013-llm-provider-and-no-langchain.md)
Related: [ADR-012 — Knowledge Graph Layer](ADR-012-knowledge-graph-layer.md), [ADR-015 — Embedding Provider](ADR-015-embedding-provider.md), [ADR-031 — Storage Boundary](ADR-031-storage-boundary-sqlite-vs-neo4j.md)

## Context

ADR-013 intentionally rejected LangChain as a runtime production dependency. That decision remains sound for the core KW Pipeline product because the project values:

- small and auditable dependency surfaces;
- explicit Pydantic schemas;
- reviewable extraction boundaries;
- predictable provider behavior;
- low long-term maintenance risk.

However, the roadmap now needs to evaluate two external approaches:

1. **LangChain / LangChain Experimental**, especially graph extraction and RAG orchestration patterns.
2. **Microsoft GraphRAG**, especially global / local search, community detection, graph summarization, and semantic distillation over large corpora.

The objective is not to replace the current architecture immediately. The objective is to benchmark these approaches against KW Pipeline's existing in-house extraction, graph projection, review gate, and future vector-search layer.

## Decision

KW Pipeline keeps the core production rule from ADR-013:

> No LangChain runtime dependency in the core production API path unless a future ADR explicitly promotes it.

But this ADR introduces an **experimental evaluation lane** where LangChain and Microsoft GraphRAG may be used under strict constraints.

## Experimental Lane Rules

LangChain and Microsoft GraphRAG may be added only when all of the following are true:

1. They are isolated behind explicit experimental modules, for example:
   - `app/experiments/langchain_eval/`
   - `app/experiments/ms_graphrag_eval/`
2. They are installed through optional dependency groups, not mandatory runtime dependencies.
3. They are disabled by default.
4. They are enabled only through explicit admin / environment flags.
5. Their outputs never bypass the existing schema validation and human review gate.
6. Their outputs are stored as experimental artifacts unless explicitly promoted by a future ADR.
7. They are benchmarked against the existing KW Pipeline extraction path using the same document set.

## Proposed Configuration Flags

Recommended flags:

```text
KW_EXPERIMENTAL_LANGCHAIN_ENABLED=false
KW_EXPERIMENTAL_MS_GRAPHRAG_ENABLED=false
KW_RAG_FRAMEWORK=kw_native | langchain_eval | ms_graphrag_eval
KW_VECTOR_STORE=neo4j | qdrant
```

The default must remain:

```text
KW_RAG_FRAMEWORK=kw_native
KW_VECTOR_STORE=neo4j
```

A future ADR may change the default only after benchmark evidence.

## What May Be Evaluated

### LangChain Evaluation

Evaluate:

- document loader orchestration;
- graph extraction patterns;
- structured extraction helpers;
- RAG chain composition;
- hybrid retrieval orchestration;
- agentic workflows for later roadmap phases.

Do not evaluate LangChain as a hidden abstraction layer over the whole product. It must remain a replaceable experiment.

### Microsoft GraphRAG Evaluation

Evaluate:

- entity and relationship extraction quality;
- topic / community detection;
- global search over large documents;
- local search around entities and claims;
- graph summarization quality;
- ability to isolate signal from document noise;
- incremental update behavior;
- cost and runtime on large documents.

Microsoft GraphRAG is not treated as a graph database. It is evaluated as a graph-based semantic extraction and retrieval methodology.

## Evaluation Criteria

Each framework must be scored against the native KW Pipeline path.

| Criterion | Description |
|---|---|
| Extraction quality | Precision and recall of extracted entities, claims, topics, and relations |
| Provenance quality | Ability to preserve source references down to section / chunk / page |
| Reviewability | Ability to route all claims through the existing HITL workflow |
| Cost | LLM tokens, embedding cost, compute time, storage overhead |
| Runtime performance | Time to ingest, extract, index, and query |
| Scalability | Behavior on large documents and multi-document corpora |
| Dependency risk | Transitive dependencies, license risk, API churn |
| Operational simplicity | Local deployment, Docker setup, debugging, observability |
| Product fit | Fit with KW Pipeline's auditable industrial knowledge objective |

## Qdrant Vector Store Evaluation

This ADR also opens the door to a second vector-store implementation.

The current Neo4j vector path is acceptable for a compact MVP because graph and vector retrieval live in one backend. However, it increases coupling to Neo4j.

KW Pipeline should introduce a `VectorStore` Protocol with at least two implementations:

```text
VectorStore
    ├── Neo4jVectorStore
    └── QdrantVectorStore
```

Recommended admin setting:

```text
KW_VECTOR_STORE=neo4j | qdrant
```

The admin UI should expose the active vector store and whether the configured provider is healthy.

Qdrant evaluation objectives:

- decouple semantic search from the graph database;
- reduce Neo4j lock-in;
- compare retrieval speed and ranking quality;
- support future scale-out architecture;
- keep commercial licensing clean through Apache 2.0.

## Semantic Extraction Score Roadmap

The semantic extraction score becomes a first-class metric.

The score should not be a single LLM confidence field. It should aggregate multiple signals:

```text
Semantic Extraction Score = weighted combination of:
- text extraction quality
- OCR confidence
- layout confidence
- source-reference coverage
- chunk coherence
- entity extraction confidence
- relation extraction confidence
- contradiction / ambiguity risk
- graph centrality / reuse signal
- reviewer feedback signal
```

Recommended first implementation:

```text
extraction_score =
  0.20 * source_reference_coverage +
  0.20 * parser_confidence +
  0.20 * chunk_coherence +
  0.20 * entity_relation_confidence +
  0.20 * review_stability
```

The exact weights should be admin-configurable later. For the MVP, hardcoded weights are acceptable if they are visible and documented.

## Promotion Rule

LangChain or Microsoft GraphRAG may be promoted from experiment to production only if a future ADR proves that it:

1. materially improves extraction quality or delivery speed;
2. does not weaken provenance and HITL controls;
3. does not create unacceptable dependency or license risk;
4. can be operated locally and in target deployment environments;
5. can be disabled or replaced without corrupting the knowledge store.

Until then, they remain evaluation tools, not architectural foundations.

## Consequences

- ADR-013 remains valid for the production path.
- The team can still benchmark LangChain and Microsoft GraphRAG without violating the architecture decision.
- Qdrant becomes an approved evaluation target as a second vector store.
- The admin surface must eventually expose vector-store and experimental-framework selection.
- Experimental results must be stored separately from validated knowledge unless promoted by review.

## Recommendation

Start with three small roadmap tickets:

1. Add `SemanticExtractionScore` as a schema field and expose it in the review UI.
2. Add `VectorStore` Protocol with `Neo4jVectorStore` and `QdrantVectorStore` implementations behind `KW_VECTOR_STORE`.
3. Add an experimental benchmark harness comparing `kw_native`, `langchain_eval`, and `ms_graphrag_eval` on the same curated document set.

Do not put LangChain or Microsoft GraphRAG in the main ingestion path until the benchmark proves it is worth the operational cost.
