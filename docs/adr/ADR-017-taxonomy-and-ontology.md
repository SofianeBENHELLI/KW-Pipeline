# ADR-017: Taxonomy and Ontology — Hybrid Auto-Deduction + Operator-Imposed

## Status

**Accepted**, 2026-05-04. Project owner ratified the hybrid mode
("clairement, on fait le choix, c'est du mode hybride") after the
post-#206 audit identified that the Knowledge Explorer's "cluster"
axis is a placeholder field nothing populates today: every document
falls into `document_type="unknown"` (`SemanticExtractor` line 64),
which collapses to a single grey blob in the Explorer regardless of
what's been ingested. The auto-deduced topic clustering
(`TopicClusteringService`, #142) lives on a parallel axis the
Explorer's adapter does not consume as the cluster source.

This ADR ratifies how taxonomy + ontology actually work in
KW-Pipeline.

## Context

KW-Pipeline already has two parallel signals that *could* drive a
"cluster" view of the corpus:

- **Auto-deduction** — `TopicClusteringService` (deterministic,
  keyword-overlap based) emits `topic` nodes in the graph. Stable
  across re-runs of the same input. No operator configuration.
- **Operator placeholder** — `DocumentProfile.document_type: str =
  "unknown"` exists in the semantic schema. Nothing populates it
  today; the field is an orphan.

Today the auto path **works** but is statistical (topics are derived
from keyword overlap, not from a metier-defined vocabulary), and the
operator path is **not wired**. The Explorer renders neither
correctly: it tries to read `document_type` (always `"unknown"`) and
then groups every document under one cluster.

Three options were considered:

| Option | Description |
|---|---|
| **A — Pure auto-déduction** | Keep only `TopicClusteringService`. Rename the Explorer axis from "cluster" to "topic" and surface the deterministic output. No operator-imposed structure. |
| **B — Pure operator-imposed** | Drop the auto path. Operators define a taxonomy (categories + subcategories). Documents are classified into the taxonomy at validation time. The Explorer renders the operator's structure only. |
| **C — Hybrid** | Auto-deduction is the default (the platform works from the first ingestion, no operator setup required). When an operator imposes a taxonomy, that taxonomy takes precedence and the Explorer renders the imposed structure for any document that has an assigned category. Documents without an assignment fall back to the auto-deduced topic. |

## Decision

### 1. **Hybrid mode (option C)** is the canonical answer

The pipeline always has *some* clustering signal:

- **Default** — every projected document is assigned an auto-deduced
  topic via `TopicClusteringService`. This is what's shipping today
  and continues to work.
- **Imposed** — when an operator publishes a taxonomy
  (`Taxonomy` resource, see §3), every projected document is
  classified into the taxonomy too. The classification result lands
  on the document as a new `taxonomy_category_id` field.
- **Precedence** — the Explorer's "cluster" axis renders the
  taxonomy category when present, the auto-deduced topic otherwise.
  Both paths populate the same Explorer slot, so the UX is uniform.

The hybrid choice is the cheapest defensible answer. The platform
demos out-of-the-box, the metier-aligned governance arc lights up
when the operator decides to invest in it, and the Explorer renders
the same axis from either source.

### 2. **Read-only in the Knowledge Explorer**

Per the project owner's stance: the Explorer **never modifies** the
taxonomy. It is a viewer of the projected knowledge layer. Editing
happens through the dedicated KnowledgeForge surface (the existing
ingestion widget + the API). A future "Open in KnowledgeForge"
deep-link from a taxonomy node in the Explorer is the only
write-side affordance.

### 3. **Taxonomy shape — tree (categories → subcategories)**

The taxonomy is an ordered tree, not a flat list and not a
free-form graph:

```yaml
taxonomy:
  schema_version: v0.1
  categories:
    - id: hr
      label: People & HR
      description: >
        Personnel policies, hiring guidance, performance reviews,
        onboarding documents.
      subcategories:
        - id: hr.hybrid_work
          label: Hybrid work policies
          description: >
            Documents describing on-site / remote / cross-border
            expectations for employees.
        - id: hr.performance
          label: Performance & review cycles
          ...
    - id: legal
      label: Legal & Risk
      ...
```

Why a tree:

- **Flat (tags)** is too poor for the metier story — operators want
  to say "this is HR > hiring", not just "hiring".
- **Graph (cross-linked concepts)** is over-engineering for v1.
  Semantic relations between categories already live on the auto
  side via `related_to` / `same_topic_as` edges; if cross-links are
  needed at the taxonomy level, they can ride on a follow-up ADR.
- **Tree** is what the operator mentally has in mind when she says
  "ranges les documents dans une structure", and it's what the
  Explorer's left rail already renders (clusters → docs → chunks).

Each category carries a `description` (free text). The description
is what the classifier reads to decide which category a chunk
belongs in (see §4).

### 4. **Classification method — embedding-based for both modes**

Per ADR-015 we already pull `voyage-3` embeddings on every chunk.
The classifier reuses them:

- **Auto-deduction (default)** — `TopicClusteringService` keeps its
  current keyword-overlap shape. No change.
- **Imposed** — for each category in the taxonomy, the operator
  writes a short description. The classifier:
  1. Embeds every category's description once at taxonomy-publish
     time (`voyage_api_key` required, same dependency the rest of
     Phase 3 has).
  2. For each chunk, computes cosine similarity between the chunk
     embedding and every category's embedding.
  3. Assigns the chunk to the top-1 category if the cosine score is
     above a configurable threshold (default `0.55`); otherwise
     leaves the chunk unassigned (i.e. falls back to the auto-
     deduced topic).
  4. The document's `taxonomy_category_id` is the most-frequently
     assigned category among its chunks.

Why not LLM-assisted classification:

- **Cost** — one Anthropic call per chunk for a corpus of
  thousands of chunks blows the LLM budget. Embedding cosine is
  pre-computed and free at classify time.
- **Determinism** — embedding cosine returns the same answer for
  the same input. Useful for audit trails (#26 / audit event store).
- **Consistency with the rest of Phase 3** — the search route
  already uses Voyage embeddings; reusing them for classification
  keeps the dependency surface tight.

A future refinement (ADR-017a) can layer an LLM "tie-breaker" pass
when the cosine score is ambiguous (e.g. two categories within
`0.05`), but the v1 path is pure cosine.

### 5. **Source of edits — YAML file for v1, API for v2**

Three options were considered for where the operator authors the
taxonomy:

| Option | Pros | Cons |
|---|---|---|
| **YAML committed to the repo** | Reproducible, versionable via git, reviewable in PRs. Zero auth surface needed. | Requires a deploy / restart to pick up changes. |
| **Admin HTTP route** (`POST /knowledge/taxonomy`) | Live updates without redeploys. | Needs auth (#83, parked). |
| **UI in KnowledgeForge** | Best operator UX. | Needs both an admin route AND a UI build-out. |

The v1 ships **YAML committed**:

- File path: `apps/api/taxonomy.yaml` (or `KW_TAXONOMY_PATH` env
  override).
- Loaded at app startup; documents are classified at validation
  time and at startup against the loaded taxonomy.
- An empty / missing file means the auto-deduction path is the
  only signal — the platform stays usable.

The admin route + UI are explicitly tracked as v2 (after auth #83
lands), with the YAML loader staying as the canonical fallback so
the platform always has a deterministic source of truth.

### 6. **Retroactivity — opt-in re-classify, never automatic**

A taxonomy change does **not** automatically re-classify the
existing corpus. Two reasons:

- **Cost** — re-classifying every chunk after a single edit can
  fire thousands of cosine computations.
- **Audit trail** — re-classification rewrites the
  `taxonomy_category_id` on every document; the operator should
  *decide* when that history rewrite happens, not have it slip in
  as a side effect of editing the YAML.

The operator triggers re-classification explicitly:

- CLI: `python -m app.cli reclassify` (lifts on the existing
  reconciliation pattern, #124).
- Future admin route: `POST /knowledge/taxonomy/reclassify`
  (deferred with the rest of the admin surface).

Newly-validated documents post-change are classified automatically
against the latest taxonomy at validation time.

### 7. **Per-tenant — parked with auth #83**

The taxonomy is global today. When the auth + workspaces story
(#83 / #91) lands, the taxonomy becomes a per-workspace object —
one YAML / DB row per workspace, no global taxonomy. The classifier
and the Explorer both read the workspace-scoped taxonomy at request
time. This ADR doesn't pre-commit the data model for that — the
v1 shape (one global taxonomy) is forward-compatible with a
per-workspace upgrade because the API contract stays the same:
"give me *a* taxonomy".

## Implementation plan

Sketched here so the PR sequence is unambiguous; each item lands as
its own PR.

| PR | Slice | Pre-requisite |
|---|---|---|
| **B2** | `Taxonomy` Pydantic model (tree) + YAML loader + `GET /knowledge/taxonomy` route + new `KW_TAXONOMY_PATH` setting. No classifier yet. | This ADR. |
| **B3** | `TaxonomyClassifier` service (embedding-based, top-1 cosine over chunks). Wired into `KnowledgeProjector` after the chunk-embedding pass. New `taxonomy_category_id` property on chunk + document graph nodes. | B2 + Voyage API key gate (already enforced). |
| **B4** | `kw-reclassify` CLI command + reconciliation hook to re-run B3 over the existing corpus. | B3. |
| **C1** | Explorer alignment: read the taxonomy via the new route, render it in the left rail when present, fall back to auto-deduced topics otherwise. Read-only. | B2 minimum. |
| **(deferred)** | Admin HTTP route + KnowledgeForge edit UI. Tracked under #83 / auth. | Auth lands. |

## Decisions still open before code

The macro decision ("hybrid") is ratified. Five sub-decisions are
**Proposed** above; if you want to push back, do so before B2 starts:

1. **Tree vs flat** — proposed tree. Push back: "non, plat suffit pour v1".
2. **Embedding-based classifier** — proposed cosine top-1 (no LLM).
   Push back: "ajoute un fallback LLM" or "règles keyword au lieu
   d'embeddings".
3. **YAML for edits** — proposed v1 YAML, API/UI later. Push back:
   "directement l'API admin behind a token".
4. **Opt-in re-classify** — proposed CLI / explicit. Push back:
   "auto-reclassify on YAML change".
5. **Default cosine threshold** — proposed `0.55`. Push back:
   "tighter (0.7)" or "looser (0.45)" — easy to tune, mostly cosmetic
   for v1.

## References

- [ADR-012](ADR-012-knowledge-graph-layer.md) — Knowledge graph layer.
- [ADR-013](ADR-013-llm-provider-and-no-langchain.md) — LLM provider.
- [ADR-015](ADR-015-embedding-provider.md) — Embedding provider
  (Voyage). The classifier reuses the same Voyage SDK that's already
  pinned by ADR-015; no new dependency.
- [#142](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/142) —
  `TopicClusteringService` (the auto-deduction path that stays).
- [#83](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/83) —
  Auth (parked); blocks the admin route in §5.
- 2026-05-04 audit — found that `document_type` is always
  `"unknown"` and the Explorer's edge mapping uses Cypher-style
  uppercase kinds the backend never emits. Drove this ADR.
