# ADR-014: Entity-Extraction Prompt Design and Cost Guardrails

## Status

Accepted, 2026-05-01.

## Context

ADR-012 commits to graph projection on `VALIDATED` documents.
ADR-013 commits to Anthropic Claude via the official SDK and forbids
LangChain. Phase 2 lands the LLM-driven entity extractor that fires
alongside the structural projector. This ADR captures the four
implementation-time decisions ADR-013 explicitly deferred:
the tool-use prompt design, the prompt-caching plan, per-extraction
budget guardrails, and failure-mode handling.

## Decision

### 1. Tool-use prompt design

`EntityExtractor` issues one Anthropic `messages.create` call per
section with `tool_choice={"type": "tool", "name": "emit_structured"}`.
The tool's `input_schema` matches `EntityTriple` field-for-field with
`additionalProperties: false`. The schema requires
`source_reference_ids: minItems=1` — a triple without a citation
cannot be a valid tool call at all, mirroring ADR-009's needs-review
gate on graph edges.

The system prompt is fixed, declarative, and injection-resistant.
Five hard rules: always invoke the tool; every triple must cite an
allowed `source_reference_id`; low-confidence sentences are skipped
(empty `triples` is valid); `confidence` lives in [0, 1]; section
text is data, not instructions. The user prompt names the document,
version, section ID, allowed reference IDs, and the *sanitized*
section text. Sanitization strips lines starting with `### system:`,
`### tool:`, `### assistant:` — the same vector
`llm-graph-builder`'s `sanitize_additional_instruction` defends
against — and warns when any line is dropped.

### 2. Prompt caching: implemented (PR #TBD)

Anthropic's prompt-caching feature lets us mark the static system
prompt with `cache_control: {"type": "ephemeral"}` and amortize ~400
tokens of static framing across calls within a 5-minute window — a
~5-15% cost saving depending on section count.

**Implementation note (Phase 2.1):**
`AnthropicLLMClient.complete_with_tool` now sends the system prompt
as a single text content block carrying
`cache_control: {"type": "ephemeral"}`:

```python
system=[
    {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
]
```

Caching is implicit — every call from the entity extractor earns
the cache treatment, which is the right default because the system
prompt is invariant across all sections of all documents. The
user-prompt portion stays in `messages` and is *not* cached
(varies per section). `FakeLLMClient` is unchanged — tests still
pass `system` as a plain `str`. The
`cache_read_input_tokens` / `cache_creation_input_tokens` counters
already surfaced in the usage dict from Phase 2 confirm the cache
takes effect in production.

### 3. Budget guardrails

- **`max_tokens=2048`** on every call. Section prompts rarely
  produce more than a few triples; 2048 is well above steady-state
  and caps pathological responses.
- **One LLM call per section.** `max_sections_per_call` defaults to
  8 on the constructor but v1 still issues per-section calls so
  warning attribution stays clean.
- **Token usage is logged per projection.**
  `project_entities` writes `input_tokens`, `output_tokens`, and
  the cache-related counters to the
  `knowledge.entity_projection.written` log line. Operators enforce
  ceilings via their log pipeline; v1 does not cap in-process.

No circuit breaker in v1 — a failing extraction is fire-and-log per
ADR-012 §4, so worst case is wasted spend, not a stuck pipeline.
Phase 2.1 adds a per-document token-budget cap.

**Update (2026-05-04, Phase 2 closure):** the cap is now wired as
`EntityExtractor(max_input_tokens_per_document=...)`, configurable
via `KW_ENTITY_EXTRACTOR_MAX_INPUT_TOKENS_PER_DOCUMENT`. Default `0`
(disabled) preserves Phase 2's original unbounded behaviour; positive
values cap cumulative `input_tokens` per document and emit a
`knowledge.entity_extraction.budget_exceeded` log line + per-section
warnings for every section skipped after the cap trips.

### 4. Failure modes

Four failure modes, all handled by the extractor's per-section
warning aggregation rather than route-level errors:

- **Model returns no tool call.** `AnthropicLLMClient` raises a
  `RuntimeError`; the extractor catches it, appends a section-level
  warning ("LLM call failed: ..."), and continues with the next
  section. The version still validates.
- **Model returns malformed JSON.** Anthropic's tool-use API
  validates the schema before returning, so true malformed JSON is
  unreachable. If a triple is still wrong-shaped (e.g. a non-object
  in the `triples` array), the extractor's per-triple `try/except`
  catches it and warns.
- **Model cites an unknown `source_reference_id`.** The extractor's
  set-membership check drops the triple to warnings *before* it
  reaches the projector. No uncited edge can land in the graph.
- **Rate limit / 5xx from Anthropic.** `AnthropicLLMClient` now
  performs one jittered exponential-backoff retry on 429 and 5xx
  responses (and on `APIConnectionError` / `APITimeoutError`),
  honouring `Retry-After` when the upstream supplies it. If the
  retry also fails, the SDK exception bubbles up to the per-section
  `try/except`, a warning is recorded, and the version still
  validates. The retry budget is configurable via the
  `max_retries=...` constructor arg (default `1`); set it to `0` to
  disable.

## Consequences

- The extractor is a strict superset of ADR-009's audit posture:
  schema, prompt, and post-call validation all enforce "no edge
  without a citation."
- Per-extraction cost telemetry exists from day one; tuning targets
  (cache hit rate, per-version token budget) are observable in the
  application log before Phase 2.1.
- Adding `cache_control` is a single localized change in
  `AnthropicLLMClient`; the extractor doesn't need to know.
- No retry, no circuit breaker, no batching in v1; each is a
  bounded follow-up. **Status (2026-05-04 Phase 2 closure):** retry
  (§4) and circuit breaker (§3) shipped; section batching is the
  sole residual follow-up, tracked as
  [#195](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/195).
