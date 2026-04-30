# ADR-009: SemanticEnricher Boundary for LLM Extraction

## Status

Accepted

## Context

ADR-001 commits the project to LLM-based semantic extraction as part of the
document intelligence MVP, but with the explicit constraint that the model
"cannot bypass schema validation." Today there is no boundary in the code
where an LLM call could plug in: `SemanticExtractor` builds rule-based
output and returns it directly, with no extension point for additional
extractors and no place to enforce the validation constraint that ADR-001
calls for.

If we add LLM integration without first defining that boundary, a
misbehaving model — or, more dangerously, a prompt-injected one returning
attacker-controlled JSON — could sneak malformed assets into the catalog.
The catalog is the system of record; corruption there is hard to roll back
and easy to miss.

This ADR defines the boundary so the follow-up that *uses* the LLM has a
single, narrow contract to fit through. No actual LLM provider integration
ships in this change.

## Decision

1. **`SemanticEnricher` Protocol.** A new
   `app.services.enrichers.SemanticEnricher` Protocol declares the shape of
   any extractor that produces additional `SemanticAsset` rows. It takes
   the immutable `RawExtraction` plus the existing rule-based `assets`
   (today `[]`; populated by future #48) and returns *additional* assets:

   ```python
   class SemanticEnricher(Protocol):
       name: str
       def enrich(
           self,
           raw_extraction: RawExtraction,
           existing_assets: list[SemanticAsset],
       ) -> list[SemanticAsset]: ...
   ```

   The Protocol is `@runtime_checkable` so tests and the dependency
   container can assert structural conformance without forcing inheritance.

2. **`SemanticExtractor` is the only invocation site.**
   `SemanticExtractor.__init__` accepts `enrichers: list[SemanticEnricher]`
   (default `[]`). After building the rule-based assets, it iterates the
   list in registration order, calling each enricher with
   `(raw_extraction, current_assets)` and union-extending the result.
   Nothing else in the codebase is allowed to call `enricher.enrich`
   directly — the boundary lives in exactly one place.

3. **All enricher output is forced to `review_status = "needs_review"`.**
   Whatever the enricher claims (including `"source_backed"` or
   `"validated"`), `SemanticExtractor` overwrites the field after the call
   returns. This is the core failure-isolation guarantee: a compromised or
   over-confident model cannot self-promote its output past human review.

4. **Schema validation runs at the boundary.** Every returned item is
   re-validated via `SemanticAsset.model_validate(...)`. Items that fail
   validation are dropped and a warning is logged with the enricher name
   and the validation error. The rest of the enricher's output is kept.

5. **Enricher exceptions are isolated.** If `enricher.enrich` raises, the
   error is logged via `logging.getLogger(__name__).exception(...)` and
   the enricher is skipped. Catalog state is unchanged. Other enrichers in
   the list still run. The pipeline does not crash on a single enricher's
   bad day.

6. **Wiring.** `dependencies.py` passes `enrichers=[]` to
   `SemanticExtractor` in both `build_services` and
   `build_persistent_services`. Tests that need a different list construct
   `SemanticExtractor` directly.

## Consequences

- Any LLM provider integration (OpenAI, Anthropic, etc.) implements the
  `SemanticEnricher` Protocol and is registered in `dependencies.py`.
  Provider-specific concerns — API keys, rate limits, retries, prompt
  templates, caching — live behind the boundary, in the enricher
  implementation, never leaking into `SemanticExtractor`.
- Reviewers always see LLM-produced assets as `needs_review`. Promotion
  to `source_backed` or `validated` happens through the existing review
  flow, not through enricher self-attestation.
- The boundary has a single test surface: feed `SemanticExtractor` an
  enricher that returns invalid output, an enricher that raises, etc.,
  and assert isolation. No need to retest these properties for each
  future provider.
- Schema migrations stay tractable: enricher output flows through the
  same `SemanticAsset` validator as everything else, so an additive
  schema change (per ADR-008) automatically applies.
- New runtime dependencies are not introduced by this change. The
  Protocol and stubs are stdlib + existing Pydantic.
