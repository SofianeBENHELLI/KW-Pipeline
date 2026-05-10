# ADR-029: AURA Companion Citation Contract

## Status

Proposed, 2026-05-10.

This ADR locks the wire shape that every grounded answer the AURA
companion produces will carry. The contract lands ahead of the
companion route itself ([#370](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/370),
under EPIC [#373](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/373)) so
downstream consumers — the web app, the widget, future 3DEXPERIENCE
embeds — encode against a stable shape from day 1.

## Context

The architecture diagram's Step 6 ("Activate in Companion") asks for
grounded answers, recommendations, decisions, and actions. Answers
are the entry point: a user types a question, the companion replies
with prose and citations back to the underlying chunks.

The shape of those citations is a contract that, once consumed by
multiple frontends, becomes painful to evolve. Three concrete risks
if we delay locking it:

1. **Frontend divergence.** Each surface invents its own citation
   wrapper, leading to incompatible hover-card / footnote rendering
   across web / widget / embed.
2. **Trust signal mismatch.** The explorer's search panel already
   renders trust labels (`validated` / `source-backed` / `candidate`)
   based on `validation_status` + `is_source_backed`. If the
   companion ships with different field names for the same concepts,
   the trust UI fragments.
3. **Feedback bridge gap.** [#371](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/371)
   needs an `answer_id` to address a past response and a
   `citation_index` to address a specific citation inside it. If the
   first companion route ships without `answer_id` baked in, every
   cached / displayed answer becomes un-addressable for feedback.

## Decision

Adopt a single response envelope, `GroundedAnswer`, for every
grounded surface the companion exposes. Schema lives in
`apps/api/app/schemas/companion.py` and is re-exported as
importable Pydantic models.

### Envelope shape (v0.1)

```jsonc
{
  "schema_version": "v0.1",
  "answer_id": "ans_01J…",          // addressable handle for feedback (#371)
  "answer": "…",                    // user-facing prose
  "citations": [Citation, …],
  "trust_summary": TrustSummary,
  "generated_at": "2026-05-10T15:30:00Z",
  "model": "claude-sonnet-4-5"
}
```

### Citation shape

```jsonc
{
  "chunk_id": "…",
  "document_id": "…",
  "version_id": "…",                // pinned to a specific version, never drifts
  "span": { "start_char": 124, "end_char": 287 } | null,
  "confidence": 0.0–1.0,             // companion's confidence the chunk supports the surrounding text
  "validation_status": "VALIDATED" | "NEEDS_REVIEW" | "REJECTED" | null,
  "is_source_backed": true | false,
  "source_url": "…" | null,
  "snippet": "…"                     // exact source text the answer paraphrases / quotes
}
```

### Trust summary

```jsonc
{
  "citation_count": 4,
  "validated_citation_count": 3,
  "source_backed_citation_count": 1,
  "candidate_citation_count": 0,
  "trust_gate_filtered_count": 0     // number of candidate citations dropped by default-deny (#372)
}
```

### Field rationales

- **`version_id` is required on every citation.** A citation that
  pins only `document_id` would silently follow the document forward
  to a newer version with different content, breaking auditability.
  Citations are immutable references to a specific chunk in a
  specific version.
- **`span` is optional.** Sentence-grain citations omit it when the
  cited chunk supports the surrounding paraphrase as a whole. When
  present, the half-open `[start_char, end_char)` interval matches
  the chunking primitives already used by the extraction pipeline.
- **Trust field names mirror the explorer search panel.**
  `validation_status` and `is_source_backed` are the same field
  names used by `ExploreSearchChunk` / `ExploreSearchDocument` so
  frontends can reuse their existing label-rendering logic without a
  translation layer.
- **`answer_id` is addressable.** ULID/UUID; lets the feedback
  bridge (#371) target a specific past response without the consumer
  having to cache the full body server-side.
- **`trust_gate_filtered_count` surfaces silent drops.** When the
  default-deny trust gate (#372) filters candidate citations out of
  the response, the count is non-zero so the UI can render "N
  candidate sources hidden — toggle to widen". This prevents the
  silent suppression failure mode where a user thinks the corpus
  has nothing to say.

## Back-compat policy

The wire contract evolves by two rules:

1. **Additive fields are non-breaking.** A new optional field on
   `Citation`, `GroundedAnswer`, or `TrustSummary` does not require
   a `schema_version` bump. Consumers that ignore unknown fields
   keep working.
2. **Renames or removals require a bump.** Any rename, removal, or
   semantic change to an existing field forces
   `GroundedAnswer.schema_version` to advance (`v0.1` → `v0.2`) and
   the old shape stays available behind a feature flag for at least
   one release so consumers can migrate.

The first companion route lands behind `schema_version="v0.1"`. If
the v0.2 cut happens before v0.1 has any consumers, the bump can be
skipped — but the rule applies the moment the route is reachable in
production.

## Out of scope (for this ADR)

- The companion `POST /companion/answer` route itself — separate
  follow-up under EPIC #373.
- Streaming response shape (SSE / WebSocket framing) — separate
  ADR when streaming lands.
- Recommendation / decision / action envelopes — those carry
  citations using this same `Citation` shape but have their own
  outer envelope.

## Consequences

- The companion implementation has a fixed contract to write
  against; the route is a small last-mile.
- The feedback bridge (#371) and trust gate (#372) issues can be
  scoped against concrete fields (`answer_id`, `trust_summary`)
  rather than pending design.
- Frontends can prototype rendering against fixture
  `GroundedAnswer` payloads before the route exists.
- Future expansion (entity-level citations, multi-hop reasoning
  trace) lands as additive fields, not rewrites.
