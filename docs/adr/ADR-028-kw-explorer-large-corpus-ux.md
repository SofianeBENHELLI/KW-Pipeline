# ADR-028: KW Explorer Large-Corpus Navigation and Relevance Model

## Status

Proposed, 2026-05-07.

This ADR captures the product and architecture direction for KW Explorer
when the catalog contains hundreds or thousands of documents. It follows
the large-corpus UX review of `apps/explorer`, `apps/web`, the knowledge
graph payload contract, and the knowledge-layer ADRs.

Implementation issues opened from this ADR:

| Area | Issue |
|---|---|
| Backend | [#310](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/310) - focused knowledge-neighborhood API |
| Backend | [#311](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/311) - relation explanation and evidence API |
| Backend | [#312](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/312) - corpus atlas summary API |
| Backend | [#313](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/313) - multi-kind semantic search |
| Backend | [#314](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/314) - graph relevance, bridge, and outlier scoring |
| Backend | [#315](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/315) - scope and trust defaults |
| Frontend | [#316](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/316) - Topic/Search Atlas default home |
| Frontend | [#317](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/317) - focused graph lens with relation budgets |
| Frontend | [#318](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/318) - relation inspector and evidence drawer |
| Frontend | [#319](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/319) - grouped semantic search experience |
| Frontend | [#320](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/320) - ranking and filter controls |
| Frontend | [#321](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/321) - large-corpus performance and truncation states |
| QA | [#322](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/322) - large-corpus fixtures and smoke coverage |

## Context

KW Pipeline has three related product surfaces:

- **KW Forge** - document ingestion/upload widget.
- **Orbital** - document review workspace for extraction, semantic
  generation, validation, rejection, and graph projection.
- **KW Explorer** - read-only exploration surface over validated
  knowledge: documents, chunks, topics, entities, relations, search,
  and graph navigation.

The current Explorer implementation already contains useful building
blocks:

- `apps/explorer` exposes `Corpus Overview`, `Concept Map`, and
  `Catalog` views.
- The graph canvas supports focus roots, depth, breadcrumb-like history,
  hover ghosting, and confidence heatmap.
- Detail panels connect documents, chunks, concepts, and source
  evidence.
- The data adapter already aggregates chunk relations into document
  relations so the canvas is not forced to show every chunk edge.

The backend knowledge model also has the right trust primitives:

- graph nodes are typed as document, version, section, chunk, topic,
  and entity;
- structural edges are separate from deterministic semantic edges and
  LLM/entity edges;
- deterministic semantic edges carry score, reason, shared keywords,
  and source chunk ids;
- LLM/entity edges require source references.

The problem is scale. A full graph is useful for a demo corpus, but a
corpus with hundreds or thousands of documents will produce too many
nodes and links. Showing the entire graph by default creates a hairball,
hides evidence, and makes trust hard to judge.

## Decision

KW Explorer should become a **Topic/Search Atlas with a graph-as-lens
interaction model**.

The Explorer must not default to rendering the entire corpus graph. The
default screen should be a high-signal atlas that helps the user choose
where to begin:

- semantic search;
- imposed taxonomy categories and computed topics;
- validated/source-backed coverage;
- recent imports and recent validations;
- bridge documents;
- surprising candidate connections;
- low-confidence or unvalidated pockets, clearly labelled.

The graph remains important, but it is a bounded explanatory lens opened
from a document, topic, chunk, entity, search result, or relation. The
graph answers focused questions such as:

- "What is this document connected to?"
- "What evidence supports this topic?"
- "Why are these two documents connected?"
- "Which chunks bridge these topics?"
- "What surprising candidate links should I inspect?"

## Information Architecture

Explorer has four primary entry points.

### 1. Atlas

The default home for large corpora. It summarizes the corpus without
loading the full graph. It should answer:

- What are the dominant topics or taxonomy categories?
- Which areas are validated and source-backed?
- What changed recently?
- Which documents bridge otherwise distant areas?
- Which candidate outliers may be worth inspecting?

### 2. Search

Search is a first-class entry point, not a small local typeahead. It
should return grouped results:

- chunks with snippets;
- documents with top contributing chunks;
- topics/categories with evidence chunks;
- entities with source-backed mentions;
- relation matches when the query maps to a reason or shared keyword.

Search results should carry score, confidence, validation/source-backed
status, and navigation targets.

### 3. Catalog

Catalog remains the operational document list. It is the best place for:

- filename/status/source/date filtering;
- version awareness;
- recently imported documents;
- review handoff back to Orbital;
- locating documents that are not yet validated.

Catalog is not the primary knowledge exploration surface.

### 4. Focused Graph Lens

The graph lens is opened from a selected object. It should render only a
bounded neighborhood around the root:

- root node;
- strongest direct relations;
- a limited number of bridge/outlier links;
- optional weak-link bundles/counts;
- omitted counts and truncation status.

The graph lens should support depth, edge budget, threshold, and
trust/filter controls. It should never require fetching the entire
corpus graph in the browser.

## Navigation Semantics

Click behavior must be predictable.

### Document click

Open document detail with:

- document metadata;
- validation/source-backed status;
- top topics/entities;
- strongest related documents;
- evidence chunks;
- version summary;
- actions: open source, focus graph, find similar, open in Orbital.

### Topic/category click

Open topic detail with:

- summary and keywords;
- document count and chunk count;
- top documents;
- evidence chunks;
- related topics;
- focus graph action.

### Chunk click

Open chunk/evidence detail with:

- source document/version;
- source reference or extraction anchor;
- text preview/snippet;
- confidence;
- linked topics/entities;
- action to highlight in the document viewer.

### Entity click

Open entity detail with:

- type and aliases when available;
- source-backed mentions;
- related entities;
- documents/chunks where the entity appears;
- focus graph action.

### Relation click

Open a relation inspector, not a navigation-only action. The inspector
must explain:

- relation kind;
- provenance class: structural, deterministic, or LLM/citation-backed;
- score/strength;
- reason;
- shared keywords;
- source chunks;
- source references;
- validation/trust status;
- contributing chunk pairs for aggregated document links.

## Graph Relevance Model

Explorer should rank and display relations using a backend-owned scoring
policy. The frontend can render visual weights, but it should not invent
the meaning of strength.

Default ranking should combine:

- relation score from graph properties;
- confidence;
- validation/source-backed status;
- number and quality of evidence chunks;
- semantic similarity to the current query/root;
- rarity of shared keywords/topics;
- bridge score;
- outlier score.

Strong links are shown by default. Weak links are counted, bundled, or
placed behind "show more" controls.

Outliers are useful, but they are not facts. They must be labelled as
candidate or surprising connections. A good outlier is a strong
source-backed relation between otherwise distant topics or taxonomy
regions.

## Progressive Disclosure

Explorer should begin clean and reveal more context only when the user
asks for it.

Default states:

- atlas shows aggregate summaries, not all nodes;
- search shows grouped top results, not all matches;
- topic detail shows top documents and top evidence;
- document detail shows strongest relations first;
- graph lens shows bounded neighborhoods;
- weak links and hidden nodes are exposed through counts, bundles, and
  explicit expansion.

Controls:

- depth;
- edge budget, such as Top 10, Top 25, Top 50;
- relation strength threshold;
- validated/source-backed toggle;
- source, document type, date, confidence, entity type, and semantic
  similarity filters;
- bridge and outlier toggles.

## Backend API Direction

The existing global `GET /knowledge/graph` route is not the right
primary API for large-corpus Explorer UX. It can remain as an operator
or compatibility surface, but new Explorer UI should rely on bounded
and ranked APIs.

Required backend surfaces:

1. **Atlas summary** - bounded corpus summaries for the default home.
2. **Focused neighborhood** - bounded graph lens rooted at a document,
   topic, chunk, entity, or relation.
3. **Relation explanation** - detailed evidence and provenance for one
   relation or aggregated document link.
4. **Multi-kind semantic search** - grouped results across chunks,
   documents, topics, entities, and relations.
5. **Relevance scoring** - deterministic ranking and classification for
   strong, weak, bridge, and outlier links.
6. **Scope/trust defaults** - new Explorer APIs must be scope-aware and
   default to validated/source-backed knowledge.

Every response should be schema-versioned and typed through OpenAPI, in
line with ADR-008 and ADR-011.

## Frontend Direction

Required frontend changes in `apps/explorer`:

1. Replace graph-first default with the Topic/Search Atlas.
2. Consume server-backed search instead of local snapshot-only search.
3. Consume focused neighborhood payloads for graph lens rendering.
4. Add relation inspector and evidence drawer.
5. Add visible ranking/filter controls.
6. Add clear truncation and omitted-count states.
7. Lazy-load document extraction/semantic detail only when selected.
8. Preserve existing useful patterns: hash deep links, focus history,
   detail panels, document viewer highlights, catalog table, and
   read-only posture.

## Trust and Explainability

Explorer must never blur reviewed knowledge and candidate knowledge.

Rules:

- validated/source-backed is the default view;
- candidate/unvalidated links are labelled and visually muted;
- every relation is explainable by source chunks, source references, or
  structural provenance;
- LLM-generated relations without citations are not shown as facts;
- outliers are labelled as candidate insights;
- Explorer remains read-only;
- validation and correction flows route back to Orbital or future
  KnowledgeForge admin surfaces.

## Consequences

### Positive

- Explorer scales to large corpora without graph hairballs.
- Users get multiple natural entry points: search, topic, catalog, and
  focused graph.
- Backend ranking semantics become testable and stable.
- Relation explanations become a product feature rather than hidden
  graph metadata.
- The UX aligns with ADR-017 hybrid taxonomy: imposed categories when
  available, computed topics otherwise.
- The graph remains powerful while becoming focused and trustworthy.

### Negative

- Requires new backend read APIs rather than only improving the
  existing frontend graph adapter.
- Requires a relevance-scoring policy that will need calibration on
  realistic corpora.
- Requires additional tests and fixtures for large-corpus behavior.
- The Explorer frontend must move away from the current assumption that
  a single snapshot can power every view.

### Neutral

- The existing Orbital document-scoped graph remains useful for review.
  It can keep using `GET /documents/{document_id}/graph`.
- `GET /knowledge/graph` can remain for operator/audit or compatibility
  flows, but it should not power the default large-corpus Explorer UX.
- The Atlas and focused graph lens can ship incrementally. Search,
  topic drill-down, and relation inspector do not all need to land in
  the same PR.

## Cross-references with the rest of the backlog

This ADR sits on top of several other tracks. The dependencies are
explicit so the implementation issues stay linkable instead of
re-deriving them at each iteration.

### Auth + workspace chain (hard prerequisite for #315)

`#315` (Explorer scope and trust defaults) is the **read-side
application** of the workspace predicate. It cannot land cleanly until
the predicate itself is on every list/search/graph route. Sequence:

```
#83 (auth identity, actor propagation)
   └─→ #91 (workspace scoping predicate everywhere)
          └─→ #315 (Explorer-side defaults: scope filter + validated/source-backed trust)
                 └─→ #310 / #311 / #312 / #313 (each must read the actor + scope context)
```

Backend Explorer endpoints (#310 / #311 / #312 / #313) can be built
against an empty-scope context first, but they MUST NOT ship to a
multi-tenant deployment until #315 lands.

### Archive / purge interaction (ADR-027)

[ADR-027](ADR-027-archive-purge-admin-tool.md) defines the archive +
purge admin surface and introduces the `SUPERSEDED` version status.
Explorer's read surfaces (atlas, search, graph lens, neighborhood)
**default to hiding `SUPERSEDED` and `ARCHIVED` versions** — those
versions stay reachable only through the version lineage panel and
the Orbital admin tool. The user-facing exploration surface should
not tease items the operator has decided to remove from the working
view.

### Taxonomy fallback (EPIC-1)

The atlas's "imposed taxonomy categories" tile depends on
[EPIC-1](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/210)
slices 1.2 / 1.3 (business taxonomy schema + persistence + LLM
allocation per chunk). Until those land, the atlas falls back to
**computed topics** from `topic_clustering.py`. Both code paths must
be implemented in `#312`'s atlas summary endpoint so the surface
ships before EPIC-1 does.

### Frontend file-refactor sequencing (#229)

`apps/explorer/src/App.tsx` (1093 LOC) and `GraphCanvas.tsx`
(1320 LOC) are flagged in `#229` (Audit P0) for splitting. The atlas
+ grouped-search + graph-lens + relation-inspector rewrite touches
the same files. **Sequence #229 before #316** — or, if calendar
forces an inverted order, scope the new Explorer surfaces under a
fresh `apps/explorer/src/features/atlas/` component tree that does
not extend the existing megafiles. Either path keeps reviewer fatigue
in check.

### Review-write boundary (#306)

Explorer is **read-only**. The relation inspector (#318) and
neighborhood lens (#317) expose evidence and citations but offer no
accept/reject controls. The reviewer write surface lives in Orbital;
the chunk-level review pane EPIC at `#306` is the
canonical write side. Two products, one knowledge graph, no overlap.

## Implementation Order

Recommended order, with the dependency notes above folded in:

0. **Prerequisites.** `#229` (file refactor) and the auth/workspace
   chain (`#83 → #91`) must be far enough along that #315 has
   somewhere real to land. Backend issues #310–#314 can begin in
   parallel against an empty scope context.
1. Backend scoring and trust/scope defaults: `#314`, `#315`.
2. Backend atlas, search, neighborhood, and relation explanation:
   `#312`, `#313`, `#310`, `#311`.
3. Frontend atlas and grouped search: `#316`, `#319`.
4. Frontend graph lens and relation inspector: `#317`, `#318`.
5. Frontend ranking/filter controls and truncation states: `#320`,
   `#321`.
6. Large-corpus fixtures and smoke coverage: `#322`.

The order is not strict, but the frontend graph lens should not depend
on raw global graph fetches while waiting for backend neighborhood
support.

## Alternatives Considered

### A. Graph-first Explorer

Open Explorer directly on the global graph.

Rejected for large corpora. It is visually impressive for small demos
but becomes noisy and hard to trust with many documents and many links.

### B. Search-only Explorer

Make Explorer only a semantic search and evidence interface.

Rejected because users also need cartography: topic coverage, bridge
documents, clusters, and relation navigation. Search should be primary,
but not alone.

### C. Catalog-first Explorer

Make Explorer open on the document table.

Rejected as the default because catalog is operational, not
exploratory. It remains important for document/version/status workflows.

### D. Topic/Search Atlas with Graph Lens

Accepted. It gives the cleanest default, preserves exploratory power,
and turns the graph into an explainable focused tool instead of a
canvas full of every possible edge.

## References

- [ADR-012](ADR-012-knowledge-graph-layer.md) - Knowledge graph layer.
- [ADR-015](ADR-015-embedding-provider.md) - Embedding provider.
- [ADR-016](ADR-016-chat-surface-mode-taxonomy.md) - Chat surface mode
  taxonomy.
- [ADR-017](ADR-017-taxonomy-and-ontology.md) - Hybrid taxonomy and
  ontology.
- [ADR-020](ADR-020-workspace-scoping.md) - Workspace scoping.
- [ADR-025](ADR-025-document-similarity-and-supersede.md) - Document
  similarity and supersede.
- [ADR-027](ADR-027-archive-purge-admin-tool.md) - Archive / purge
  admin tool. Explorer hides `SUPERSEDED` and `ARCHIVED` versions
  from default views; the admin surface is where they remain
  reachable.
- [Knowledge graph payload contract](../architecture/knowledge_graph_payload.md).
- [Knowledge layer architecture](../architecture/knowledge_layer.md).
