# ADR-015: Embedding Provider for Phase 3 Vector RAG

## Status

**Accepted**, 2026-05-03. Project owner confirmed Voyage AI as the v1
provider; Phase 3 implementation work is unblocked. Originally
proposed 2026-05-02.

This ADR is the embedding-provider half of the Phase 3 chat-surface
prereqs that ADR-013 explicitly deferred ("Embedding provider for
Phase 3's vector RAG mode. Phase 3 ADR.") and that
[`docs/roadmap/mvp_backlog_review.md`](../roadmap/mvp_backlog_review.md)
flagged as a still-open decision.

## Context

ADR-012 §3 sketches a Phase 3 chat surface with three modes — pure RAG
(vector retrieval over chunks), GraphRAG (Cypher-guided retrieval), and
hybrid. The vector mode needs an embedding model: a way to map a
`SemanticSection`/`ChunkRecord` and a user query into the same
vector space so cosine-similarity retrieval lands the right chunks.

ADR-013 commits to **one provider** for the v1 LLM surface — Anthropic
Claude — with an `LLMClient` Protocol so a second provider is one impl
away. Embeddings are a different surface (the model class is unrelated
to the chat model) and the choice is independent of the LLM choice.
Picking an embedding provider per the same "small, optional, behind a
Protocol" pattern keeps Phase 3 honest about its dependency footprint.

Two things constrain the decision today:

1. **Anthropic does not ship a first-party embeddings API as of the
   ADR-013 / 2026-05-01 audit pass.** Anthropic's recommended embedding
   partner is **Voyage AI** (the same vendor that powers Claude's
   tool-use evaluation pipelines internally per their public docs).
   If/when Anthropic launches a first-party endpoint, the Protocol
   means swapping is mechanical.
2. **No auth surface yet** (#83). Whatever provider we pick, its API
   key joins `ANTHROPIC_API_KEY` as another env-var-scoped secret.
   Two providers means two keys, two billing dashboards, two outage
   surfaces. Rolling Voyage in is one secret on top of one we already
   have, not zero.

## Decision

### 1. Default embedding provider: **Voyage AI** (`voyage-3`)

Phase 3 ships with **Voyage AI** as the embedding provider. Reasons:

- **Closest cultural fit.** Voyage is a single-purpose embeddings
  vendor (no LLM ambitions) — exactly the "thin client over a
  documented HTTP API, no transitive ML stack" stance ADR-013 took for
  the LLM. The `voyageai` Python SDK is < 1 MB; no LangChain dependency
  comes along.
- **Anthropic-recommended.** Anthropic's own docs point users to Voyage
  for embeddings. Operationally we stay inside the Anthropic-blessed
  stack instead of expanding to a second LLM vendor.
- **Quality is competitive.** `voyage-3` ranks at or above
  `text-embedding-3-large` on MTEB English retrieval at the time of
  writing (2026-05). Either would meet Phase 3's quality bar; the
  ranking difference is within margin for practical retrieval.
- **Cost is friendly.** ~$0.06 per 1M tokens at GA pricing; one of the
  cheaper SOTA cloud embedding endpoints. The chunk volume KW Pipeline
  is sized for (small/medium MVP) lands well under any reasonable
  monthly budget.

### 2. Behind an `EmbeddingClient` Protocol — same pattern as ADR-013

The provider is wrapped in an `EmbeddingClient` Protocol in
`app.services.knowledge.embedding_client` so call sites depend on the
Protocol, not on `voyageai.Client` directly. Adding OpenAI / Anthropic
(when launched) / local later is one new concrete impl plus a wiring
change in `app.dependencies`.

The Protocol:

```python
class EmbeddingClient(Protocol):
    name: str  # "voyage-3", "text-embedding-3-large", "bge-large-en-v1.5", …
    dim: int   # vector dimensionality, for the index schema

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...
    def embed_query(self, query: str) -> list[float]: ...
```

Two methods because asymmetric models (separate document / query encoders)
are common; for symmetric models the second routes to the first.

### 3. Vector index: Neo4j 5 native vector index

Phase 1 already ships Neo4j (ADR-012). Neo4j 5.13+ supports a native
HNSW vector index on node properties via `db.index.vector.create_index`.
Phase 3's vector mode stores the embedding as a `chunk.embedding`
property and queries via `db.index.vector.queryNodes`. **No second
vector store** (Pinecone / Qdrant / pgvector) — the deployment footprint
stays at one database for catalog metadata + graph + vectors.

This keeps the operator story simple: Neo4j is already the failure
domain for the knowledge layer; the vector index lives in the same
domain.

### 4. Configuration discipline

- New env vars (read through `app.settings.Settings`):
  - `VOYAGE_API_KEY` — required for Phase 3 vector mode.
  - `KW_EMBEDDING_MODEL` — model id (default `voyage-3`).
- Phase 3 vector mode refuses to construct without `VOYAGE_API_KEY`,
  same way Phase 2 entity extraction refuses without
  `ANTHROPIC_API_KEY`. Phase 1 + Phase 2 + the existing pipeline run
  without `VOYAGE_API_KEY` set; the embedding client never instantiates
  in that path.
- The settings model gains `voyage_api_key` and `embedding_model`
  fields; the existing alias-choices pattern keeps the unprefixed
  `VOYAGE_API_KEY` working alongside a future `KW_VOYAGE_API_KEY`.

## Rejected alternatives

### OpenAI `text-embedding-3-large`

Equally fine on quality, slightly higher per-token cost, and would mean
adding OpenAI as our second LLM-vendor secret + outage surface. The
strongest argument for OpenAI is "everyone has an OpenAI key" — true,
but not enough on its own to justify the second vendor relationship
when Voyage covers the same need at lower cost.

Easy follow-up if Voyage proves insufficient: write a second
`EmbeddingClient` impl, flip `KW_EMBEDDING_PROVIDER` to `openai`. About
~50 lines of code.

### Local `sentence-transformers` (`bge-large-en-v1.5` / `mxbai-embed-large`)

Self-hosted, no per-call cost, no external dep at runtime. Two
disqualifying issues for v1:

- **Heavy install.** `sentence-transformers` pulls in `torch`,
  `transformers`, `safetensors`, `tokenizers`, plus model weights
  (~1–2 GB on disk). The current `apps/api` dep set is intentionally
  small (eight runtime packages); adding ~2 GB and a CUDA optionality
  doubles the deployment surface.
- **Quality gap.** The best open-weight models trail SOTA cloud
  embeddings on retrieval benchmarks (MTEB) by enough that retrieval
  recall would noticeably suffer. Phase 3 is the user-visible chat
  surface; precision matters.

Reconsider if a hard offline/air-gap requirement lands.

### Ollama embedding endpoints (`mxbai-embed-large`, `nomic-embed-text`)

Same offline benefits as `sentence-transformers`, much lighter install
(no torch — Ollama bundles the runtime). Disqualifies on production
robustness: Ollama is great for dev, mediocre for production load
(no built-in autoscaling, single-process server). Quality is still
behind cloud SOTA. Same reconsider trigger as `sentence-transformers`.

### Anthropic first-party embeddings (when available)

If Anthropic launches a first-party embeddings API before Phase 3
ships, **switch to it** without re-litigating this ADR — the Protocol
makes it a one-impl swap, and it eliminates the Voyage secret. The
default in this ADR is conditional: Voyage today, Anthropic the day
they launch.

## Consequences

- **One new optional runtime dependency** in Phase 3: `voyageai`. Same
  shape as `anthropic` from ADR-013 — packaged in `pyproject.toml`,
  refuses to construct without `VOYAGE_API_KEY`, dormant unless an
  operator opts in.
- **Neo4j requirement stays at 5.13+.** The vector index is a 5.13
  feature; the `docker-compose.yml` already pins 5.23 Community so this
  is satisfied.
- **No second vector store** in the deployment topology.
- **Tests stay deterministic.** A `FakeEmbeddingClient` (returns a
  pre-recorded vector for each known input string) covers the unit
  suite. Real Voyage calls live behind a `pytest -m
  embedding_integration` marker, opt-in only, mirroring the
  `pytest -m llm_integration` pattern from ADR-013.
- **Multi-provider stays cheap.** Adding OpenAI / local / Anthropic is
  one Protocol impl plus a wiring change.

## What this ADR does not decide

- **Specific Voyage model version** beyond the `voyage-3` default.
  Phase 3 implementation can pick `voyage-3-large` or `voyage-code-2`
  if benchmarks justify; the env var lets operators override.
- **Chunking strategy for embedding.** ADR-012 §3 already commits to
  embedding chunks (1:1 with `SemanticSection` today, per #144).
  Whether to also embed topic summaries is a Phase 3 implementation
  call, not an ADR.
- **Vector index parameters.** HNSW `ef`/`M` tuning lands at
  Phase 3 implementation time, not here.
- **Cost guardrails / circuit breakers.** Same posture as ADR-014 for
  the LLM. Phase 3 implementation ADR (if needed) will enumerate.
- **Cache policy.** Whether to cache embeddings keyed by
  `(model_id, sha256(text))` to skip re-embedding unchanged chunks.
  Probably yes; Phase 3 implementation call.

## Acceptance criteria for this ADR

Both criteria met as of 2026-05-03:

1. ✅ The project owner confirmed Voyage AI is acceptable as the v1
   provider.
2. ✅ Phase 3 tracking issue filed:
   [#186 — Vector RAG (chunk indexing + Neo4j vector index +
   /knowledge/search)](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/186).
   Implementation scaffolding (settings field, optional `voyageai`
   dep, `EmbeddingClient` Protocol, `FakeEmbeddingClient`) already
   lives on `main` so #186 can pick up directly from a configured
   baseline.
