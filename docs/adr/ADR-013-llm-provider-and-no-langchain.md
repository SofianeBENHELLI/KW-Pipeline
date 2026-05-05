# ADR-013: LLM Provider — Anthropic Claude via the Official SDK; No LangChain

## Status

Accepted, 2026-05-01.
Amended (§6 — multi-provider), 2026-05-05.

## Context

ADR-012 commits to building a knowledge layer on top of validated
documents. Phase 2 (entity extraction) and Phase 3 (chat) need an LLM
provider behind a `SemanticEnricher` (ADR-009) or an analogous
boundary. Two questions need answers before we write any LLM code:

1. **Which provider?** Anthropic, OpenAI, Google Vertex AI, AWS
   Bedrock, Azure OpenAI, Ollama, or something else? llm-graph-builder
   supports ten and uses LangChain to abstract over them.
2. **Which abstraction?** LangChain's `LLMGraphTransformer` and
   `GraphCypherQAChain` are turn-key for the patterns we want.
   Adopting them would let us reuse llm-graph-builder's prompt designs
   verbatim. The cost is the LangChain dependency surface and the
   structural opinions that come with it.

This ADR captures both decisions before any LLM code is written so
the implementation can proceed without re-litigating them per phase.

## Decision

### 1. One provider in v1: Anthropic Claude via the `anthropic` SDK

Phase 2 and Phase 3 use **Anthropic Claude** via the official `anthropic`
Python SDK. No multi-provider abstraction in v1.

Rationale:

- One provider keeps the v1 review surface small. Multi-provider
  support is a maintenance commitment (per-provider rate limits,
  quirks, structured-output formats, billing telemetry).
- The `anthropic` SDK is a thin client over a documented HTTP API.
  No transitive ML stack, no model downloads, no LangChain. Pip
  install is single-digit MB.
- Claude 4.5 supports structured output via tool-use, which is the
  pattern we want for entity/relation extraction (the model emits
  validated JSON conforming to a Pydantic schema). The other
  candidates (OpenAI, Vertex) all support equivalents — adopting them
  later is mechanical.

The provider is wrapped behind an `LLMClient` Protocol in
`app.services.knowledge.llm_client` so call sites depend on a Protocol,
not on `anthropic.Anthropic` directly. Adding a second provider later
means writing a second concrete implementation of the same Protocol —
and at that point we lift the multi-provider factory pattern from
`llm-graph-builder/backend/src/llm.py::get_llm()` and apply it on our
side. Not before.

Rejected alternatives:

- **OpenAI first**: equally fine technically. Anthropic is chosen for
  alignment with the rest of this codebase's tooling stack and
  Anthropic's stronger tool-use schema enforcement at the time of
  writing. Either provider is one Protocol implementation away.
- **Multi-provider day-one (llm-graph-builder pattern)**: premature
  generality. We can adopt their factory shape the day we need a
  second provider; the cost of adding it then is small.
- **Local model via Ollama**: tempting for offline dev but the
  structured-output story is materially weaker. Defer.

### 2. No LangChain. Reimplement directly against the SDK.

KW Pipeline does **not** take a runtime dependency on `langchain`,
`langchain-experimental`, `langchain-anthropic`, or any
`langchain-*` package. The Phase 2 entity extractor and Phase 3 chat
service are reimplemented directly against `anthropic` + Pydantic.

Rationale:

- **Dependency footprint.** `langchain` plus
  `langchain-experimental` (which is where `LLMGraphTransformer`
  lives) pulls a transitive closure that includes `langsmith`,
  `tenacity`, `pydantic` (already present), `tiktoken`,
  `sqlalchemy`, and a handful of others. KW Pipeline's culture
  (visible in
  [pyproject.toml](../../apps/api/pyproject.toml)) is intentionally
  minimal-deps — eight runtime packages today, all small. Doubling
  the install size for one prompt template is the wrong trade.
- **Auditability.** The patterns we want from
  `LLMGraphTransformer` are: (a) a system prompt that asks for
  entities and relations, (b) JSON schema enforcement on the
  response, (c) optional `allowedNodes` / `allowedRelationship`
  filters, (d) sanitization against prompt injection. The whole
  thing is ~200 lines of straightforward code when reimplemented
  directly. Vendoring those 200 lines as auditable Python in
  `app/services/knowledge/entity_extractor.py` is more in keeping
  with KW Pipeline's "every model claim is reviewable" stance than
  hiding them inside a LangChain chain.
- **API stability.** `langchain_experimental` is, by name,
  experimental — the `LLMGraphTransformer` API has changed
  meaningfully across minor versions in the past year. A direct SDK
  call changes only when the SDK does, which is much less often.
- **Testability.** The `LLMClient` Protocol takes a list of messages
  and returns a typed response. Tests substitute a fake that returns
  recorded fixtures. No need to mock LangChain's internal chain
  composition.

Rejected alternative: **adopt LangChain to ship faster.** The
acceleration is real — `LLMGraphTransformer` is ready-made — but the
cost is borne forever (deps, audit footprint, API churn). The 200
lines we save up front are not worth the long-tail maintenance cost.

### 3. What we vendor from llm-graph-builder

This ADR commits us to study, not lift. Specifically:

| llm-graph-builder file | What we read | What we reimplement |
|---|---|---|
| `backend/src/llm.py` (`LLMGraphTransformer`, `sanitize_additional_instruction`) | Prompt structure, entity/relation JSON schema, prompt-injection sanitization patterns | `app/services/knowledge/entity_extractor.py` (~200 lines, Phase 2) |
| `backend/src/QA_integration.py` (RAG / GraphRAG / Vector mode dispatching) | Mode taxonomy, Cypher generation prompt, source-attribution payload | `app/services/knowledge/chat_service.py` (Phase 3) |
| `backend/src/shared/schema_extraction.py` (LLM-driven taxonomy inference) | Optional Phase 4+ pattern for #22/#23 | (Deferred) |

Source attribution for borrowed patterns lives in module docstrings:
"Adapted from neo4j-labs/llm-graph-builder
([file path], Apache-2.0)."

## Consequences

- **One new optional runtime dependency** in Phase 2: `anthropic`. It
  is *optional* in the sense that the package is added to
  `pyproject.toml` but the entity extractor and chat service refuse
  to construct without an `ANTHROPIC_API_KEY` env var. Phase 1
  (graph projection) and the existing pipeline run without
  `ANTHROPIC_API_KEY` set.
- **Zero LangChain anywhere.** No `langchain`,
  `langchain-experimental`, `langchain-anthropic`,
  `langchain-community`. No LangSmith. CI failure if someone adds one
  of these to `pyproject.toml` without amending this ADR.
- **Tests are deterministic.** The unit suite uses
  `FakeLLMClient` with recorded responses. A separate
  `pytest -m llm_integration` job (not in default CI) exercises real
  Anthropic calls; it is opt-in to avoid flaking the main suite on
  upstream rate limits.
- **Cost telemetry is in-house.** We log token counts (input,
  output, cache-read, cache-creation) per LLM call to the
  application logger. ADR-014 will detail the prompt-caching policy
  and budget guardrails when Phase 2 lands.
- **Multi-provider remains an option.** When the day comes, we add a
  second `LLMClient` implementation alongside the first. The factory
  pattern from llm-graph-builder is small and well-understood; we
  adopt it then, not now.

## What this ADR does not decide

- **Specific Claude model version** (e.g. Claude Opus 4.5 vs Claude
  Sonnet 4.6). Picked at Phase 2 implementation time based on
  cost/quality on the entity-extraction benchmark.
- **Prompt-caching policy** (which static blocks earn
  `cache_control: {"type": "ephemeral"}`). Phase 2 ADR.
- **Budget guardrails and circuit breakers.** Phase 2 ADR.
- **Embedding provider** for Phase 3's vector RAG mode. Phase 3 ADR.

## 6. Amendment (2026-05-05) — Multi-provider, Gemini primary, Anthropic fallback

§1 above committed to "one provider in v1" with the explicit caveat
that "adding a second provider later means writing a second concrete
implementation of the same Protocol — and at that point we lift the
multi-provider factory pattern". This amendment is that point.

### Decision

Two providers are supported behind the `LLMClient` Protocol:

- **Gemini** (`google-genai` SDK, `GeminiLLMClient`) — primary in
  deployments that opt in.
- **Anthropic** (`anthropic` SDK, `AnthropicLLMClient`) — fallback
  and the original v1 implementation; unchanged.

Selection is driven by `KW_LLM_PROVIDER` (`auto` / `gemini` /
`anthropic`) and the configured API keys:

- `auto` (default): Gemini wins when `GEMINI_API_KEY` is set,
  otherwise Anthropic if `ANTHROPIC_API_KEY` is set, otherwise no LLM
  (Phase 1 only).
- `gemini` / `anthropic`: pin the choice; resolution returns no
  client when the pinned provider's key is missing rather than
  silently falling back to the other provider. Operators who pin
  typically want a misconfiguration to surface as a 503 on
  `POST /knowledge/chat`, not a quiet provider switch.

`KW_LLM_MODEL` continues to alias `KW_ANTHROPIC_MODEL`; `KW_GEMINI_MODEL`
overrides the Gemini model id (default `gemini-2.5-flash` — the cheap
+ fast tier matching Sonnet's cost/quality slot).

### What does NOT change

- **No LangChain anywhere.** The §2 commitment stands. The Gemini
  implementation talks to `google-genai` directly; no
  `langchain-google-genai`, no `langchain-core` in the install graph.
- **Protocol surface.** `complete_with_tool` and `complete_text` are
  unchanged. Call sites (entity extractor, chat service, route
  layer) compile against the Protocol and are unaware of which
  provider answers.
- **Token-usage shape.** The dict returned by both methods carries
  the same four keys (`input_tokens`, `output_tokens`,
  `cache_read_input_tokens`, `cache_creation_input_tokens`) so the
  audit logs and ADR-014 §3 circuit breaker stay provider-agnostic.
  Gemini's `cached_content_token_count` maps onto
  `cache_read_input_tokens`; Gemini does not surface a distinct
  `cache_creation` counter and the field stays at 0.
- **No runtime failover.** A process picks one provider at startup.
  Quota exhaustion on Gemini does not transparently retry against
  Anthropic — the operator sees the upstream error and decides. A
  future `FailoverLLMClient` is contemplated but deliberately not
  built today; doubling the latency on every call to chase rare
  upstream failures isn't worth it at this stage.
- **Prompt caching on Gemini.** Anthropic prompt caching (ADR-014
  §2) stays as-is. Gemini's context-cache API (`cachedContents`) is
  a different shape — create-then-reference — and is intentionally
  out of scope for this amendment. Phase 2 cost on Gemini will be
  higher than on Anthropic with caching until that follow-up lands.

### Rejected alternatives

- **Provider failover at runtime.** Tried in spirit by passing the
  primary's failure to the secondary's call inside one Protocol
  invocation. Rejected: doubles tail latency on real failures,
  obscures which provider produced an answer in the audit trail,
  and adds a cross-provider error-classification layer for marginal
  resilience benefit.
- **Replace Anthropic outright.** Smaller diff, but loses the
  fallback safety net and forces every existing test fixture +
  integration job to be rewritten against Gemini before this lands.
- **Add a third provider (OpenAI / Vertex / Bedrock) in the same
  PR.** Out of scope. The Protocol still permits it; adding a third
  is one more `LLMClient` implementation when the need is real.

### Consequences

- **One additional optional runtime dependency:** `google-genai`.
  Same posture as `anthropic` — added to `pyproject.toml`, lazy-imported
  inside `GeminiLLMClient.__init__`, dormant unless
  `GEMINI_API_KEY` is set.
- **Settings surface gains three fields:** `gemini_api_key`,
  `gemini_model`, `llm_provider`. Defaults preserve every existing
  deployment's behaviour exactly: a deployment with only
  `ANTHROPIC_API_KEY` set continues to use Anthropic with
  `llm_provider=auto`.
- **`GET /admin/config`** now exposes both providers' configured-flag
  + model id and the resolved `active_provider`, so the Settings
  widget can render "Gemini in use, Anthropic available as fallback"
  without re-implementing the resolution rules.
- **Tests stay deterministic.** `FakeLLMClient` covers both providers
  in unit tests. `GeminiLLMClient` accepts an injected mock client
  the same way `AnthropicLLMClient` does. The opt-in
  `pytest -m llm_integration` job is the place to add a real Gemini
  smoke test when needed; no default-suite change.
