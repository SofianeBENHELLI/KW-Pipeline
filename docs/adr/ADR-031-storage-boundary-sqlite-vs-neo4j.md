# ADR-031: Storage Boundary — SQLite vs Neo4j

## Status

Accepted, 2026-05-10.

This ADR sets the long-term boundary between the two persistence
backends in KW Pipeline. It supersedes the implicit "knowledge layer
is optional, falls back to in-memory" posture the codebase has had
since Phase 1, which was correct for the MVP and is incorrect at the
target scale (100k–millions of chunks).

## Context

Storage today (per the inventory exercise on 2026-05-10):

- **SQLite** holds documents, versions, raw extractions, semantic
  documents (JSON), validation metadata, scopes, audit events, and
  the periodic catalog backup target.
- **Neo4j** (or `InMemoryGraphStore` when Neo4j is not configured)
  holds chunk nodes, topic nodes, entity nodes, every relation
  (structural / deterministic / LLM-derived), chunk embeddings, and
  the vector index used for semantic search.

Two problems with the status quo:

1. **The boundary is muddy.** The Neo4j chunk node carries a
   `text_preview` property that duplicates content already living in
   `semantic_documents.payload.sections[].text`. Small today, but a
   wedge for future drift — every additional duplicated field
   doubles the surface area for the two stores to disagree.
2. **The "knowledge layer is optional" framing is wrong for
   production at scale.** At 100k+ chunks the in-memory graph store
   loses every edge / topic / embedding on restart, and SQLite is
   not (and shouldn't be) the home of vector indexes. Operators
   running a real corpus need Neo4j; pretending it's optional means
   shipping a config that boots successfully into a degraded state
   the user didn't ask for.

## Decision

### Source-of-truth rule

| Concern | Backend | Rationale |
|---|---|---|
| Documents, versions, lifecycle status | SQLite | Relational, transactional, audited. |
| Raw uploaded bytes | Filesystem (or future object store) | Bytes don't belong in either DB. |
| Raw extraction output (per version) | SQLite (JSON column) | Source of truth for what the parser produced. |
| Semantic document, including section text (= chunk text) | SQLite (JSON column) | Source of truth for what the semantic generator produced. **The text of a chunk lives here, not in Neo4j.** |
| Validation metadata, scopes, audit | SQLite | Governance surface. |
| Imposed taxonomy (after #379 lands) | SQLite | Versioned, audited. |
| Aggregated document↔document relations cache (after #380 lands) | SQLite | Derived index for fast Explorer reads. |
| **Chunk nodes (graph identity, lightweight metadata)** | **Neo4j** | First-class queryable graph entities; required for traversal, neighborhood queries, similarity search. |
| **Chunk embeddings + vector index** | **Neo4j** | Vector index is what Neo4j is good at; SQLite is not. |
| **Topic nodes** | **Neo4j** | Same. |
| **Entity nodes** | **Neo4j** | Same. |
| **All relations (structural / deterministic / LLM)** | **Neo4j** | Edges are the primary access pattern; native graph storage. |

The single-sentence rule:

> **SQLite is the truth for "what was uploaded, parsed, validated,
> governed." Neo4j is the truth for "what does it mean and how does
> it relate."**

### What chunks carry in Neo4j

Chunk nodes carry only graph-shaped metadata:

- `chunk_id` (= `section_id`)
- `document_id`, `version_id` (denormalised so graph queries don't
  need to join back to SQLite)
- `heading` (short label for the canvas)
- `char_count` (used by ranking heuristics)
- `keywords[]` (deterministic-extraction keywords used by
  `same_topic_as` / `shares_keyword` edges)
- `topic_id` (membership reference)
- `text_preview` — **explicit exception, see below.**

**No chunk `text` (the full body) on the node.** Consumers that need
the full text fetch from `semantic_documents.payload.sections[]`.

#### The `text_preview` exception

`text_preview` (max 200 chars, derived from the chunk's text on
projection) lives on the Neo4j node as the **search snippet cache**.
The search route returns it inline so the Explorer can render
result rows without an extra round-trip per hit.

This is NOT a duplicate source of truth: it is a **derived,
bounded, read-only cache** that the projector regenerates from
`semantic_documents.payload.sections[].text` on every re-projection.
The truth is the SQLite section text; `text_preview` is its
view-model for the search response.

If a future contributor proposes adding more chunk text fields to
the Neo4j node ("just a few more characters" / "for richer
snippets"), the answer is no — those go in the search response
shaped from a SQLite read, not on the node. The 200-char snippet is
the line.

### Production posture

Neo4j is **mandatory in production**. A production boot that has
the knowledge layer enabled but no Neo4j configured is a
mis-configuration and must fail fast with a remediation message
pointing at this ADR.

### Dev / test posture

`InMemoryGraphStore` stays in the codebase as a developer
affordance:

- Tests run against it by default — fast, no external dependency.
- Local development can run against it for the in-memory demo
  (`KW_PERSISTENT=false`).
- It is **never** acceptable in production. The boot guard above
  enforces this.

## Consequences

### Immediate

- Add a boot check that fails when `KW_PERSISTENT=true` AND
  `KW_KNOWLEDGE_LAYER_ENABLED=true` AND no Neo4j config is
  present. Production deployments must be explicit about wiring
  Neo4j; silently falling back to `InMemoryGraphStore` in
  production was never the intent.
- Document the boundary so future PRs adding new data kinds have a
  clear default: SQL for governance / lifecycle / derived caches;
  Neo4j for graph-native primitives (chunks, embeddings, edges).

### Downstream

- Future contributors have a clear rule for "where does X go." New
  data kinds map naturally onto the source-of-truth principle.
- Disaster recovery has two distinct stories: SQLite (backup file
  + restore — already covered by `KW_BACKUP_INTERVAL_SECONDS`,
  PR #357) and Neo4j (operator must take Neo4j backups; helper
  filed as #381 to ship in a follow-up). Neither store is more
  prone to disaster than the other; they fail differently and need
  separate backup strategies.
- The aggregated-document-relations cache (#380) writes into SQLite
  as a derived view, never into Neo4j — this stays consistent with
  the source-of-truth rule (the cache is a *derived* artefact, and
  derived artefacts that need joins / pagination / filters belong
  in SQL).

### Out of scope

- Migrating to a different SQL store (Postgres, etc.) — orthogonal,
  unblocked by this boundary.
- Replacing Neo4j with a different graph / vector store — also
  orthogonal; the abstraction (`GraphStore` Protocol) already
  permits it.
- A Neo4j backup helper — filed as separate issue #381.
