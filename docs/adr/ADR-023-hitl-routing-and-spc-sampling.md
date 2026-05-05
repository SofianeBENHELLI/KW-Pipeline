# ADR-023: HITL Routing Policy + SPC Sampling Math (5-Signal Confidence Score)

## Status

**Proposed**, 2026-05-05. Codifies the "smart HITL router" decisions
from EPIC-A
([#215](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/215)),
sliced so that this ADR + the matching `ConfidenceScorer` +
`ValidationMetadata` persistence land first; the actual routing
decision (`hitl_router.py`) is the next slice and is intentionally
out of scope here. Lands on top of the topic-clustering primitive
([#142](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/142),
ADR-025 §3) and the actor-on-audit work ([#83 slice
1](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/83), ADR-019
§4) — without those, the per-version metadata this ADR persists would
have neither a topic axis to bucket on nor an attributed audit row to
sit beside.

## Context

KW Pipeline's review gate is binary today: every version that lands
in `NEEDS_REVIEW` waits for a human to push it to `VALIDATED` or
`REJECTED` from Orbital. That posture is the right default for the
pilot, but it forces a reviewer to look at every single version even
when the semantic output is obviously good — perfectly chunked, no
orphan sections, topic-coherent, every asset cited. The throughput
ceiling becomes the reviewer's day, not the platform's.

EPIC-A asks for a smarter routing layer that:

1. Computes a **confidence score** for every `NEEDS_REVIEW` version
   using a small set of explainable signals.
2. Routes high-confidence versions through an **auto-validate** path
   so reviewers only see the ambiguous ones.
3. Keeps the auto-rate honest with **statistical process control
   (SPC) sampling** — a configurable fraction of auto-validated
   versions is still sent to a human as a quality probe.
4. Stores every routing decision + the confidence breakdown in a
   `ValidationMetadata` sidecar so "why was this version
   auto-validated?" is a SQL query.

Per Q4 of the 2026-05-04 Q&A round
([`docs/roadmap/2026-05-04-hitl-and-extensions.md`](../roadmap/2026-05-04-hitl-and-extensions.md)
§1), HITL config is **deployment-level, not per-scope** — a single
global `KW_HITL_*` env-var family governs every scope (ADR-020 §3
records the same decision from the scoping side). This ADR commits
to that shape so a future per-scope override can ride on top
without a data-model change.

This ADR codifies the policy + signal definitions + default weights +
threshold + the sidecar persistence shape. It does **not** ship the
router that consumes them — that is the next slice. Until the router
lands, the scorer runs as a fire-and-log side-effect of the
`NEEDS_REVIEW` transition: every version is scored and the metadata
upserted, but routing behaviour is unchanged (every version still
goes to Orbital regardless of score). This keeps the demos shippable
while the data needed to tune the router accumulates in the audit
trail.

## Decision

### 1. Five signals, each in `[0.0, 1.0]` with 1.0 = best

The score is a linear combination of five signals computed per
version. Every signal returns a value in `[0.0, 1.0]` where `1.0`
means "no evidence of trouble" and `0.0` means "this version is
suspicious"; the overall score follows the same convention.

#### Signal 1 — OCR flag (hard override)

If the version was produced via OCR (the `OCR` flag set during
ingestion), confidence is **forced to `0.0`** and the
`ocr_override_active` field on `ConfidenceScore` is set to `True`.
OCR'd content is too noisy for any of the other signals to be
trustworthy and the policy is "always human review" until the OCR
parser quality bar is met (#47). The override is a hard stop, not a
weight: even if every other signal scores 1.0, the version still
routes to Orbital.

The OCR flag is read from the version's raw extraction metadata. In
the v1 wiring there is no OCR parser yet (PDFs that are scanned
images surface as empty pages with a warning per
`apps/api/app/services/parsers/pdf.py`), so the flag is sourced from
a future `version.is_ocr` boolean. Until that flag exists, the
override is structurally dead but the contract is documented for the
slice that lights it up.

#### Signal 2 — Orphan chunk ratio

The fraction of chunks in the version's projection with **no
incoming or outgoing relationships** in the knowledge graph
(no `related_to` / `shares_keyword` / `same_topic_as` / `belongs_to`
edges). Orphan chunks usually mean the document is a series of
unrelated snippets — a manifest, a bullet-list of unrelated
clauses — which is exactly the shape a reviewer needs to look at.

```
orphan_ratio = orphan_chunk_count / total_chunk_count
signal_value = 1.0 - orphan_ratio
```

A document with zero chunks scores `1.0` (nothing to be orphan-of —
the empty case is benign here, the assets-coverage signal handles the
"but is it empty?" question).

#### Signal 3 — Section length z-score

For each section, compute `z = (length - mean) / stddev` against
**corpus norms bucketed by `(content_type, topic_cluster)`**. Sections
whose `|z|` exceeds a threshold (`_LENGTH_Z_THRESHOLD = 2.5`) are
"length-anomalous" — a 50-page chapter mixed in with 1-paragraph
sections, or vice versa. The signal is the fraction of sections
within threshold:

```
within_threshold = sections with |z| ≤ 2.5
signal_value = within_threshold_count / total_section_count
```

The `(content_type, topic_cluster)` bucket matters because a "policy"
PDF in the "compliance" cluster has a different length distribution
than a "specs" DOCX in the "engineering" cluster. The
`CorpusNormsProvider` Protocol (§4) owns the lookup. A bucket that
has no norms yet (cold-start, freshly-seeded deployment) scores `1.0`
for that section — we don't penalise the corpus for not knowing what
"normal" looks like yet.

A document with zero sections scores `1.0` for the same empty-case
reason as the orphan signal.

#### Signal 4 — Topic incoherence ratio

The fraction of chunks whose topic id differs from the document's
**dominant topic cluster** (the topic id that the most chunks belong
to in this version). A topic-coherent document concentrates its
chunks on one or two topics; an incoherent one spreads chunks across
N unrelated topics, which is a strong "this isn't really one
document" signal.

```
dominant_topic = mode(chunk.topic_id for chunk in chunks)
incoherence = chunks with topic_id != dominant_topic
signal_value = 1.0 - incoherence_count / chunks_with_topic_count
```

Chunks with no topic membership (singletons that didn't cluster) are
**excluded** from the denominator — they're already counted by the
orphan signal. A document where no chunk has a topic at all (every
chunk is a singleton) scores `1.0` for incoherence and the orphan
signal absorbs the penalty instead. This avoids double-counting the
same shape across two signals.

#### Signal 5 — Citation coverage (with asset-count fallback)

When **Phase 2 entity extraction is on** (`KW_ANTHROPIC_API_KEY` set
+ knowledge layer enabled), citation coverage is the fraction of
extracted entities whose `source_reference_id` is non-empty:

```
cited = entities with source_reference_id != ""
signal_value = cited_count / total_entity_count
```

When Phase 2 is off, the entity set is empty by construction and the
metric carries no signal. The fallback is the **asset-count z-score**
against corpus norms bucketed by `(content_type, topic_cluster)`,
mirroring §3 — a document that surfaces 200 assets when comparable
documents surface 5 is suspicious for a different reason (over-eager
extractor, mis-classified content type) than a document with the
expected count. Same `_LENGTH_Z_THRESHOLD = 2.5` cutoff and same
"unknown bucket scores 1.0" cold-start rule.

### 2. Default weights: equal, env-tunable, normalised at runtime

Default weight for each signal is `0.2` (equal). Operators tune via
five env vars:

| Env var | Default | Description |
|---|---|---|
| `KW_HITL_WEIGHT_OCR` | `0.2` | OCR override weight (the hard override is independent of weight; the weight controls how much OCR-related info — once we have it — contributes to the score when the override is *not* active). |
| `KW_HITL_WEIGHT_ORPHAN_RATIO` | `0.2` | Orphan chunk ratio weight. |
| `KW_HITL_WEIGHT_LENGTH_Z` | `0.2` | Section length z-score weight. |
| `KW_HITL_WEIGHT_TOPIC_INCOHERENCE` | `0.2` | Topic incoherence weight. |
| `KW_HITL_WEIGHT_CITATION_COVERAGE` | `0.2` | Citation coverage (with asset-count fallback) weight. |

Weights are **normalised at runtime** so operators can pass any
positive scale (`{0.5, 0.5, 1.0, 1.0, 2.0}` is treated identically to
`{0.1, 0.1, 0.2, 0.2, 0.4}`). Negative weights or all-zero weights
raise on construction — those are operator misconfigurations the
service refuses to start with rather than silently flatten.

```
overall = sum(weight_i * signal_i for signal_i in signals)
       where sum(weight_i) == 1.0 after normalisation
```

The OCR override is applied **after** the weighted sum: if the OCR
flag is set, `overall := 0.0` regardless of every other signal value.

### 3. Auto-validate threshold

```
KW_HITL_AUTO_VALIDATE_THRESHOLD  # default 0.85
```

The scorer **does not enforce** the threshold — it returns a
`ConfidenceScore` and the next slice's `hitl_router.py` reads it to
decide. Keeping enforcement out of the scorer means the threshold
can be retuned without bouncing the scoring path, and the metadata
stays comparable across threshold changes (the score for any given
version is independent of the cutoff in effect when it was computed).

The threshold **is** read by the scorer for one purpose only: it
lands on the persisted `ConfidenceScore.weights` payload as the
ambient policy at scoring time, so an audit query "what threshold
was active when this version was scored?" doesn't require correlating
with deploy logs.

### 4. `ValidationMetadata` persistence — sidecar table

Per EPIC-A A.5, every version's confidence breakdown is persisted
for audit + SPC sampling. Schema (Pydantic):

```python
class ConfidenceScore(APISchemaModel):
    overall: float                          # [0.0, 1.0]
    signals: dict[str, float]               # 5 raw signal values
    weights: dict[str, float]               # the (normalised) weights used
    ocr_override_active: bool
    computed_at: datetime
    computed_by_version: str                # scorer version, e.g. "v1"


class ValidationMetadata(APISchemaModel):
    version_id: str
    confidence_score: ConfidenceScore | None
    routing_decision: Literal["auto", "human", "external"] | None  # set by hitl_router (next slice)
    validation_method: Literal["auto", "human", "external"] | None
    validation_actor: str | None
```

Stored sidecar in a new `validation_metadata` table (one row per
`version_id`, foreign-keyed to `document_versions`):

```sql
CREATE TABLE validation_metadata (
    version_id        TEXT PRIMARY KEY,
    confidence_overall REAL,
    confidence_signals TEXT,    -- JSON dict {signal_name: float}
    confidence_weights TEXT,    -- JSON dict {signal_name: float}
    ocr_override_active INTEGER,
    confidence_computed_at TEXT,
    confidence_computed_by_version TEXT,
    routing_decision TEXT,
    validation_method TEXT,
    validation_actor TEXT,
    FOREIGN KEY (version_id) REFERENCES document_versions(id)
);
```

Why sidecar, not extra columns on `document_versions`:

- **Schema growth is local.** The metadata vocabulary will grow as
  we add SPC sampling fields, drift indicators, per-version model
  versions, etc. A sidecar lets that vocabulary expand without
  bloating the catalog's hot-path row.
- **Composability with no-delete policy.** The catalog's
  no-delete soft-archive policy
  ([ADR-020 §4](ADR-020-workspace-scoping.md)) treats
  `document_versions` rows as immutable history. Routing decisions
  are mutable in their lifecycle (a `human` decision today might
  be revisited by an external reviewer tomorrow); putting them on
  the catalog row blurs that distinction.
- **Internal-only by construction.** Sidecar storage keeps the
  metadata off the public `Document` / `DocumentVersion` API
  response shapes, which matches EPIC-A's "auto-validated ==
  human-validated to consumers" rule out of the box.

The accompanying `corpus_norms` table backs the length / asset-count
z-score signals. Schema:

```sql
CREATE TABLE corpus_norms (
    content_type    TEXT NOT NULL,
    topic_cluster   TEXT NOT NULL,
    metric_name     TEXT NOT NULL,         -- 'section_length' | 'asset_count'
    sample_count    INTEGER NOT NULL,
    mean            REAL NOT NULL,
    stddev          REAL NOT NULL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (content_type, topic_cluster, metric_name)
);
```

The norms table is materialised on-demand: the first request for a
bucket triggers a one-time scan of the catalog's existing semantic
documents to compute `(mean, stddev)` for that bucket and persist
the row. Subsequent reads are O(1). A future slice will add a
nightly recompute job; v1 lives with whatever the bucket looked like
at first use, which is good enough for the pilot.

### 5. Default behaviour — fire-and-log, no routing change today

Until `hitl_router.py` lands, the scorer runs as a **fire-and-log
side-effect** of the `NEEDS_REVIEW` transition (the same discipline
the knowledge projector uses per ADR-012 §3). Concretely:

1. `SemanticOutputService.generate` lands the version in
   `NEEDS_REVIEW` via `DocumentService.mark_semantic_ready`.
2. After the FSM write, the wiring layer invokes
   `ConfidenceScorer.score(...)` and
   `ValidationMetadataStore.upsert(...)`.
3. The audit handler records a `confidence.scored` event carrying
   `{document_id, version_id, overall, signals, weights, actor}`
   so the breakdown is queryable from the audit table without
   reading the sidecar.
4. **Routing behaviour is unchanged** — every version still routes
   to Orbital. The router slice will read the persisted metadata
   to decide.

The scorer is opt-out via `KW_HITL_DISABLE_SCORER=true`. When
disabled, the fire-and-log step is a no-op and no audit event lands.
This is the "demo safety" switch: if the scorer ever throws on a
demo fixture in front of a customer, we want one env var to
deactivate it without rolling back the deploy.

A scorer failure (Pydantic validation error, divide-by-zero in
corpus norms, anything unexpected) is **caught and logged** via the
same fire-and-log discipline; the `NEEDS_REVIEW` transition stays
durable. The catalog is the source of truth, the metadata catches
up via re-scoring on the router's read path or an out-of-band
reconciliation job — same shape as the knowledge projector's
recovery story.

### 6. SPC sampling math (deferred to the router slice)

The router slice will configure an SPC sampling rate
(`KW_HITL_SPC_SAMPLE_RATE`, default `0.05`). A configurable fraction
of versions that *would* auto-validate are instead routed to a human
as a quality probe; the human's decision is compared to the router's
intended decision and a discrepancy ticks a per-bucket counter.

The math is deferred because (a) the router slice owns the actual
sampling decision and (b) the discrepancy counter needs the routing
fields on `validation_metadata` populated, which only happens once
the router writes them. This ADR records the contract — every
sampled version's `validation_method` is `"human"` and a future
column on the metadata row distinguishes "human because score was
low" from "human because SPC sampled" — but defers the implementation.

## Consequences

- **Positive — audit trail for routing decisions even when humans
  are in the loop.** Every version has a confidence score and a
  routing decision attached, regardless of whether the router is
  enforcing yet. When the router lights up, we can replay the audit
  trail to compare the router's would-be decisions against the
  reviewers' actual decisions and tune weights before flipping the
  switch.
- **Positive — opt-out is one env var.** `KW_HITL_DISABLE_SCORER=true`
  makes the scorer a no-op. The "I'm about to demo to a customer
  and I don't trust this code path yet" escape hatch is one
  variable, not a code rollback.
- **Positive — sidecar table keeps the public API surface clean.**
  Consumers of `GET /documents/{id}` see the same shape they always
  saw. The "auto-validated == human-validated to consumers"
  invariant from EPIC-A holds by construction, not by a route-layer
  filter that someone might forget to add to the next route.
- **Negative — extra compute per version.** The five signals each
  walk the version's chunks/sections/entities; the corpus-norms
  lookup is O(1) after the first hit but pays a one-time scan per
  bucket. Bench on a typical 5-section, 50-chunk version: sub-100ms
  on the in-memory store, dominated by the topic-clustering call
  the projector already ran. Acceptable for the FSM transition path;
  if profiling shows it dominating in production, the scorer can
  move to a background-job queue without changing the metadata
  contract.
- **Negative — sidecar table grows linearly with versions.** One
  row per version, indefinitely. At pilot volume this is invisible;
  at production scale a vacuum / retention strategy is needed.
  Tracked as a future concern; the no-delete policy already commits
  to soft-archive for the catalog, and the same shape applies here.
- **Neutral — five env vars for tuning.** Operators have to learn
  one weight family. Documented in `.env.example` and `Settings`
  docstrings; the defaults work without configuration.
- **Neutral — corpus-norms cold start.** Unknown buckets score
  `1.0` for the length / asset signals, which is permissive on
  first deploy. The router slice's threshold is robust to this
  (the other three signals still pull the score around), and the
  norms warm up as soon as the catalog has more than a handful of
  versions per bucket.

## Alternatives considered

### Single-signal heuristic

Pick one signal (orphan ratio, or topic incoherence) and threshold
on it directly. **Rejected.** A single signal is brittle — every
heuristic has a class of false positives and a class of false
negatives that the others compensate for. Linear combination with
tunable weights gives operators the dial they need to make the
trade-off explicit without rewriting the scorer.

### ML classifier (gradient-boosted trees over the signals)

Train a model on labelled `NEEDS_REVIEW → VALIDATED/REJECTED`
outcomes and predict the validation outcome. **Deferred.** Needs a
labelled dataset we don't have yet — the labels only become
available *after* the scorer is running and the audit trail
accumulates the human decisions. A v2 ADR can add the classifier
on top of the metadata this v1 persists; it's a strict
forward-compatible extension. Until then, the linear combination is
explainable, deterministic, and doesn't need a model deploy story.

### No scoring — every version still goes to Orbital

Skip EPIC-A entirely. **Rejected.** The throughput ceiling is the
reviewer's day, and the audit trail of "why was this auto-validated?"
is independently valuable for governance even if the auto-rate
stays at 0% indefinitely (the router slice can ship with a
threshold of `1.0` and the metadata still answers the audit
question).

### Per-scope HITL config

A separate `KW_HITL_*` family per scope so different teams can
tune their thresholds independently. **Rejected**, mirroring
ADR-020 §3. The SPC bucket is `(content_type, topic_cluster)`,
not scope — the same content type and topic cluster behave
identically regardless of which scope the document lives in.
Per-scope rules would multiply the admin surface combinatorially
with no offsetting product value.

## References

- [#215](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/215) —
  EPIC-A — Smart HITL routing. The parent epic this ADR codifies.
- [`docs/roadmap/2026-05-04-hitl-and-extensions.md`](../roadmap/2026-05-04-hitl-and-extensions.md)
  §1 — Source-of-truth roadmap entry that frames the slicing.
- [ADR-012](ADR-012-knowledge-graph-layer.md) — Knowledge graph
  layer. Source of the fire-and-log discipline reused for the
  scorer side-effect.
- [ADR-019](ADR-019-authentication-and-authorization.md) §4 —
  Actor-on-audit contract. The scorer's `confidence.scored` event
  carries the same `actor` field so attribution stays consistent.
- [ADR-020](ADR-020-workspace-scoping.md) §3 — Confirms HITL config
  is global, not per-scope.
- [ADR-025](ADR-025-document-similarity-and-supersede.md) §3 —
  Topic-clustering primitive (`topic_clustering.py`, #142) that the
  topic-incoherence signal consumes.
- [#47](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/47) —
  OCR support. The `OCR` flag this ADR routes around will be set
  by that work.
- [#83](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/83) —
  Auth implementation. Slice 1 (#245) lands the `actor` field the
  `confidence.scored` event records.
