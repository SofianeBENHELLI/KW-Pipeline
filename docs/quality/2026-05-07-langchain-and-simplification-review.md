# LangChain + Simplification Review

Reviewed: 2026-05-07

## Executive recommendation

Do **not** adopt LangChain as a runtime dependency for KW Pipeline right now.

The current architecture is already built around the better abstraction for this product:
explicit, auditable service boundaries. `LLMClient` plus direct provider implementations
keeps model calls narrow, fakeable in tests, and easy to inspect. That fits the core product
promise: governed document intelligence where model-produced claims remain source-backed,
schema-validated, and reviewable.

LangChain is most useful when the product is primarily an agent or tool-calling application.
KW Pipeline is currently a deterministic ingestion, parsing, review, projection, and retrieval
pipeline with optional LLM enrichment. In this shape, a framework would mostly add dependency
surface and indirection around code that is already small enough to own directly.

## Current architecture strengths

- `LLMClient` isolates model providers from extraction and chat call sites.
- Provider implementations are directly testable without mocking LangChain internals.
- `EntityExtractor` keeps citation validation, prompt-injection sanitization, and structured
  output enforcement visible in first-party code.
- `KnowledgeChatService` is a small, explicit RAG / GraphRAG / hybrid dispatcher rather than an
  agent loop.
- `ADR-013` already captures the no-LangChain reasoning and was later amended cleanly for the
  Gemini + Anthropic provider posture without changing call sites.
- The default test path stays deterministic through fake LLM and embedding clients.

## Why LangChain is not the simplification lever

LangChain would not remove the main complexity currently visible in the repo. The complexity is
mostly in application wiring, persistence variants, graph-store behavior, route/service
boundaries, and cross-app frontend duplication. Those are product-specific seams that a generic
LLM framework will not simplify.

The main cost of adopting LangChain now would be:

- a larger transitive dependency surface;
- another abstraction layer between review-critical code and model output;
- less direct control over structured-output, citation, and audit behavior;
- more API churn risk around components that are not central to the pipeline;
- duplicated abstractions, because the repo already has `LLMClient`, `GraphStore`,
  `EmbeddingClient`, and domain-specific services.

## When to reconsider LangGraph or LangChain

Revisit **LangGraph**, not plain LangChain, if KW Pipeline grows true agentic workflows:

- long-running multi-step research tasks;
- resumable human-in-the-loop workflows with checkpointing;
- tool-calling loops where the model decides the next action;
- user-visible execution traces;
- background investigations that need durable state and interrupts.

Until that exists, direct SDK calls behind local Protocols are simpler and more auditable.

## Highest-value simplification proposals

### 1. Deduplicate service construction

`build_services()` and `build_persistent_services()` are nearly identical. Extract a shared
factory that accepts the storage/catalog/idempotency/audit/norm-store choices, then performs the
common wiring once.

Suggested shape:

- keep `build_services()` and `build_persistent_services()` as public entry points;
- introduce an internal `_ServiceBackend` dataclass carrying concrete store choices;
- introduce `_build_pipeline_services(settings, backend)` for the shared construction;
- keep in-memory and persistent differences at the edge.

Expected payoff: fewer accidental drift bugs when new services are added, and a much smaller
`dependencies.py`.

### 2. Split `PipelineServices` into service groups

`PipelineServices` has become a whole-app container. Split it into nested groups:

- `CoreServices`: storage, documents, parsers, extraction, semantic outputs, markdown;
- `KnowledgeServices`: graph store, projector, entity extractor, embeddings, search, chat,
  taxonomy;
- `HitlServices`: scorer, router, auto-promoter, sampling state, validation metadata,
  corpus norms;
- `SecurityServices`: auth, audit, idempotency.

Keep a compatibility facade if many routes/tests currently expect `services.documents` directly.
The first PR can introduce grouped fields while preserving existing attribute accessors.

### 3. Split LLM providers out of `llm_client.py`

`llm_client.py` is large because it contains the Protocol, Anthropic client, Gemini client, retry
helpers, and fake client. Keep the Protocol and shared test fake in `llm_client.py`, then move
provider implementations to:

- `llm_anthropic.py`
- `llm_gemini.py`

This preserves the no-LangChain boundary while making provider-specific behavior easier to review.

### 4. Type loose tuple and dict boundaries

Replace high-signal loose shapes with small dataclasses or Pydantic internal models:

- graph chat triples: replace `tuple[str, GraphNode, GraphEdge, GraphNode]` with
  `GraphTripleContext`;
- token usage dict: use a `TokenUsage` dataclass with `add()` / `empty()` helpers;
- entity extraction raw tool output: introduce an internal typed parser before creating
  `EntityTriple`.

Expected payoff: less defensive branching, clearer tests, and more mypy help.

### 5. Move route orchestration into services

Some route handlers still perform service-level work such as scope filtering, feature-gate
remediation, response reshaping, and access-cache management. Push reusable behavior into
services or route helpers so routes mostly:

1. validate HTTP inputs;
2. call one service method;
3. translate known exceptions to public error envelopes.

The `knowledge` routes are the best first target because search/chat filtering currently has
domain logic at the HTTP layer.

### 6. Keep performance simplification separate from framework decisions

The real hot path is graph projection and retrieval, not LLM orchestration:

- batch Neo4j embedding writes;
- keep or improve reverse indexes for graph cleanup;
- cap or persist process-local embedding cache;
- add latency breakdown logs for embedding, graph query, and LLM phases.

These changes will improve production behavior more than adopting an LLM framework.

## Suggested work order

1. Deduplicate service construction.
2. Split `PipelineServices` into grouped containers.
3. Split LLM provider files.
4. Add typed internal shapes for token usage and chat graph triples.
5. Move knowledge search/chat scope filtering out of routes.
6. Continue graph hot-path performance work.

## Decision guardrail

Do not add `langchain`, `langchain-core`, `langchain-community`,
`langchain-experimental`, `langchain-anthropic`, `langchain-google-genai`, or LangSmith-related
runtime dependencies unless `ADR-013` is amended first with a concrete agentic workflow that
requires them.
