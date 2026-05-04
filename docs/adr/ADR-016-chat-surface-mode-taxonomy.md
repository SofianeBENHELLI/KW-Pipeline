# ADR-016: Chat Surface Mode Taxonomy and Route Shape

## Status

**Accepted**, 2026-05-04. Project owner confirmed the unified-route
shape during the post-audit follow-up review on
[#205](https://github.com/SofianeBENHELLI/KW-Pipeline/pull/205) (slice
A.2, commit `c85eda6`). Supersedes the alternative route shape
proposed in [#204](https://github.com/SofianeBENHELLI/KW-Pipeline/pull/204)
(closed without merging in favour of the unified route).

## Context

ADR-012 §3 sketches a Phase 3 chat surface with three retrieval modes:

- **RAG** — top-K cosine retrieval over the chunk index produced by
  `KnowledgeProjector` + `EmbeddingClient` (ADR-015).
- **GraphRAG** — traversal of the projected `(:Document)-(:Section)-(:Entity)`
  subgraph, anchored on the documents the vector search surfaced.
- **Hybrid** — both contexts concatenated.

Two route shapes were prototyped during the audit follow-up:

1. **Per-mode top-level prefix.** `POST /chat/rag`, `POST /chat/graph`,
   `POST /chat/hybrid` — three endpoints, one per mode, each with a
   per-mode response model. This is what
   [#204](https://github.com/SofianeBENHELLI/KW-Pipeline/pull/204) shipped
   for RAG only, with the other two routes deferred.
2. **Single endpoint with body discriminator.** `POST /knowledge/chat`
   with a `ChatRequest.mode: "rag" | "graph" | "hybrid"` field on the
   request body, sharing the `/knowledge/*` prefix with the existing
   `/knowledge/search` and `/knowledge/graph` read routes. This is what
   [#205](https://github.com/SofianeBENHELLI/KW-Pipeline/pull/205) ships
   today.

Both shapes share the same retrieval primitives, the same
`KnowledgeChatService` orchestrator, and the same `KW_CHAT_DISABLED`
gating envelope when `ANTHROPIC_API_KEY` or `VOYAGE_API_KEY` is unset.
The decision is purely about the public route surface.

## Decision

### 1. Single endpoint: **`POST /knowledge/chat`**, mode is body data

The chat surface is one route. The retrieval mode lives in the request
body as a `ChatMode` literal:

```python
ChatMode = Literal["rag", "graph", "hybrid"]


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    mode: ChatMode = "rag"
    top_k: int = Field(default=5, ge=1, le=20)
```

The response is one model — `ChatResponse` — that carries the answer,
the citations the prompt was grounded in, the embedding + LLM models
used, and the typed `mode` echoed back.

### 2. Lives under `/knowledge/*`, not under `/chat/*`

The chat surface joins `GET /knowledge/search` and `GET /knowledge/graph`
under the `/knowledge/*` prefix. The prefix names the **knowledge layer**
(the Phase 1+2+3 stack of projector + entity extractor + vector index +
chat) rather than naming any one shape of HTTP interaction.

### 3. Route registration carries `operation_id="chat_with_knowledge"`

Per ADR-011, every route gets a stable `operation_id` so the
generated TypeScript client (`apps/web/src/api/generated/schema.ts`)
exposes a fixed name. The chat method on the typed client is
`POST("/knowledge/chat")` with a body shape generated from
`ChatRequest`.

## Why one route, not three

- **Adding a fourth mode never touches the route table.** A future
  "agentic" mode that loops the LLM with tool-use against the graph is
  a new branch in `KnowledgeChatService._build_context` and a new
  literal in `ChatMode`. The OpenAPI snapshot grows by one enum value,
  not by one new route.
- **Front-end mode toggle is body data, not URL data.** The
  `<ChatModeToggle/>` component (slice A.3) flips `ChatMode` on the
  pending request and re-submits. With three routes the toggle would
  switch which fetch helper is called; with one route it is one
  parameter on one helper, which mirrors how the user mentally
  experiences a "mode switch" — same question, different retrieval.
- **Disabled-state copy stays uniform.** The `KW_CHAT_DISABLED` 503
  envelope ships once, not three times. With three routes each route's
  503 copy would drift over time.
- **Idempotency-Key (when added) cleans up.** The request body already
  fully determines the response; an idempotency-key replay does not
  need to disambiguate three URLs.

## Why not per-mode routes

- **Mode-as-resource is the wrong noun.** The resource is the
  knowledge layer; the mode is a verb-adjective on a single
  question-answering verb. REST collapses to a single endpoint when
  the resource doesn't change between modes.
- **Operation-id soup.** Three routes mean `chat_rag`, `chat_graph`,
  `chat_hybrid` in the OpenAPI surface, which the generated client
  exposes as three separate functions. Call sites then either pick one
  at the top of a feature (locking the mode at compile time) or branch
  on the user's mode at every call site (which is what they were going
  to do anyway).
- **No discriminator in the response.** With three routes, callers
  have to remember which they called to interpret the response. With
  one route, the response echoes `mode` so the renderer can branch on
  data, not on call-site memory.

## Consequences

### Positive

- One typed client function (`askKnowledgeChat`) covers every mode.
  `<ChatPanel/>` does not need three branches; the `<ChatModeToggle/>`
  changes one body field.
- Fourth-and-beyond modes (e.g. an agentic loop, a Cypher-translation
  mode, a domain-specialised mode) are zero-route additions.
- The `/knowledge/*` prefix is the canonical home for the knowledge
  layer; new knowledge-layer surfaces (export, reconciliation operator
  endpoints, …) join it without having to redo the prefix story.

### Negative

- **OpenAPI consumers that drive their UI off the route table see one
  endpoint, not three.** A "list every chat mode" UI would have to read
  the `ChatMode` enum from the generated schema rather than scanning
  routes. Acceptable — every ADR-011-style codegen consumer already
  reads the schema components, not just the path table.
- **Per-mode rate limiting is one route-level rule on the body's
  `mode` field, not three route-level rules.** When per-tenant /
  per-mode quotas land, the gate has to read the body to bucket — a
  small extra step compared to "rate-limit `POST /chat/graph` to N
  RPS." Acceptable; the rate-limit story isn't built yet, so the
  decision can ship with whatever shape the auth layer (#83) needs.

### Neutral

- **Empty-retrieval short-circuit is a service-level choice, not a
  route-level one.** [#204](https://github.com/SofianeBENHELLI/KW-Pipeline/pull/204)
  proposed a deterministic "no relevant content" reply when vector
  retrieval returns zero results, skipping the LLM call entirely. The
  `/knowledge/chat` route currently always calls the LLM with an
  empty context block, relying on the system prompt's "I don't have
  enough context to answer that" rule. Adding the short-circuit is a
  small follow-up commit on `KnowledgeChatService.answer` and does not
  affect the route shape.

## Alternatives considered

### A. Three routes, one per mode (proposed in #204)

`POST /chat/rag`, `POST /chat/graph`, `POST /chat/hybrid`. Each
returns a per-mode response model (`ChatRagResponse`,
`ChatGraphResponse`, `ChatHybridResponse`).

Rejected because every advantage (per-mode rate limiting,
mode-specific response shape) materialises into a service-layer
concern at most, and every cost (route-table sprawl, drifting 503
copy, three client functions) is paid every day by every reviewer.

### B. Mode in the URL path

`POST /knowledge/chat/{mode}`. One route handler, dispatch on a path
parameter.

Rejected because the path parameter is structurally identical to a
body discriminator but worse for caching: an HTTP cache layer (when
introduced) treats `/chat/rag` and `/chat/graph` as different
resources by URL, which is cosmetic if the body is the same shape and
misleading if it isn't.

### C. Mode as a query parameter

`POST /knowledge/chat?mode=rag`. Body-and-query mix.

Rejected because POST endpoints with side effects already commit to a
body envelope; spreading parameters across body and query is a form
of API surface fragmentation that the rest of the codebase avoids
(`/knowledge/search` puts everything in the query because it's a GET;
`/documents/upload` puts everything in the body because it's a POST).

## Implementation notes

- `apps/api/app/services/knowledge/chat_service.py` ::
  `KnowledgeChatService.answer(question, *, mode, top_k)` is the
  service-layer entry point. The route is a thin shim that returns
  503 with `KW_CHAT_DISABLED` when any of the three gates (knowledge
  layer enabled, Anthropic key, Voyage key) is missing.
- `apps/web/src/features/chat/ChatPanel.tsx` calls
  `askKnowledgeChat(question, { mode, top_k, signal })`. The
  `<ChatModeToggle/>` is pure presentation; the parent owns the mode
  state.
- The widget chat panel (follow-up after this ADR) reuses the same
  client helper and the same response shape; no widget-specific
  branching beyond layout.
- ADR-013's "no LangChain anywhere in the install graph" still holds.
  The mode dispatch is a small Python switch, not a framework.

## References

- [ADR-012](ADR-012-knowledge-graph-layer.md) — Knowledge graph layer.
- [ADR-013](ADR-013-llm-provider-and-no-langchain.md) — LLM provider
  choice and the no-LangChain stance.
- [ADR-015](ADR-015-embedding-provider.md) — Embedding provider for
  Phase 3 vector RAG.
- [#205](https://github.com/SofianeBENHELLI/KW-Pipeline/pull/205) —
  Audit follow-ups PR; slice A.2 ships the unified route.
- [#204](https://github.com/SofianeBENHELLI/KW-Pipeline/pull/204) —
  Closed; proposed the per-mode-route alternative.
