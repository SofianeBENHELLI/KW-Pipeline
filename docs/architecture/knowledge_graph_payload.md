# Knowledge Graph Payload Contract — Demo KG (v0.2)

> **Status.** Draft contract for the P0 Demo KG cluster (issues
> [#140](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/140)
> through
> [#152](https://github.com/SofianeBENHELLI/KW-Pipeline/issues/152)).
> Locks the wire shape that backend (lanes A + B), demo/QA (lane C),
> and frontend (lane D) all build against. Schema version
> `KNOWLEDGE_GRAPH_SCHEMA_VERSION = "v0.2"`. The v0.1 wire payload is
> still valid; this document only describes the additive surface.

## Why this doc exists

Issue #140 ships the schema; #141/#142 emit data into it; #143 carves
the projector to write it; #144 wires the projector into validation;
#148 mirrors the types on the frontend. If those four lanes negotiate
the shape ad-hoc, three of them will rebase. This doc is the contract
they pin to.

The authoritative source remains
[`apps/api/app/schemas/knowledge.py`](../../apps/api/app/schemas/knowledge.py)
and the OpenAPI snapshot at
[`apps/api/openapi.json`](../../apps/api/openapi.json). When this
markdown disagrees with the Pydantic models, the models win.

## What changed in v0.2

- **Two new node kinds:** `chunk`, `topic`.
- **Six new edge kinds.** Three structural (`has_version`,
  `has_chunk`, `belongs_to`) and three deterministic-semantic
  (`related_to`, `shares_keyword`, `same_topic_as`).
- **Property values widened** to include `list[str]`. Topic
  `keywords` and chunk-relation `shared_keywords` travel as native
  arrays so the typed `openapi-fetch` client doesn't have to split a
  delimited string.
- **Five typed property models** (`ChunkNodeProperties`,
  `TopicNodeProperties`, `ChunkRelationEdgeProperties`,
  `TopicMembershipEdgeProperties`, `StructuralEdgeProperties`) document
  the stable property shapes. They are construction helpers, not the
  wire shape — `GraphNode.properties` and `GraphEdge.properties` stay
  flat dicts so v0.1 payloads continue to validate.

Every existing v0.1 producer or consumer keeps working. The Phase 2
`has_entity` edge contract is unchanged.

## Node taxonomy

| Kind        | `id` source                                              | `properties` model         | Producer                          |
| ----------- | -------------------------------------------------------- | -------------------------- | --------------------------------- |
| `document`  | `Document.id`                                            | (ad-hoc — see projector)   | `KnowledgeProjector.project`      |
| `version`   | `DocumentVersion.id`                                     | (ad-hoc)                   | `KnowledgeProjector.project`      |
| `section`   | `SemanticSection.id`                                     | (ad-hoc — legacy)          | `KnowledgeProjector.project`      |
| `chunk`     | `SemanticSection.id` (1:1 with section today)            | `ChunkNodeProperties`      | `project_chunks` stage (#143/#144) |
| `topic`     | deterministic id from clustering service (#142)          | `TopicNodeProperties`      | `project_topics` stage (#143/#144) |
| `entity`    | `sha256(type::text)[:16]` prefixed `entity-`             | (ad-hoc — Phase 2)         | `project_entities` (Phase 2)      |

`section` and `chunk` co-exist intentionally during the rollout. The
chunk node is the first-class demo unit; the section node stays so
existing tests pass and downstream consumers that already navigate
`part_of` chains keep working. Whether `section` is eventually
deprecated is out of scope for #140.

## Edge taxonomy and provenance rules

| Kind             | Direction                       | Provenance requirement                                    | Property model                       |
| ---------------- | ------------------------------- | --------------------------------------------------------- | ------------------------------------ |
| `part_of`        | child → parent (legacy)         | none (structural)                                          | `StructuralEdgeProperties`           |
| `has_version`    | document → version              | none (structural)                                          | `StructuralEdgeProperties`           |
| `has_chunk`      | version → chunk                 | none (structural)                                          | `StructuralEdgeProperties`           |
| `belongs_to`     | chunk → topic                   | none (structural)                                          | `TopicMembershipEdgeProperties`      |
| `related_to`     | chunk → chunk (undirected pair) | `source_chunk_ids` + `reason` + `shared_keywords`         | `ChunkRelationEdgeProperties`        |
| `shares_keyword` | chunk → chunk                   | `source_chunk_ids` + `reason` + `shared_keywords`         | `ChunkRelationEdgeProperties`        |
| `same_topic_as`  | chunk → chunk                   | `source_chunk_ids` + `reason` + `shared_keywords`         | `ChunkRelationEdgeProperties`        |
| `has_entity`     | entity → entity                 | `source_reference_id` from catalog (ADR-012 §4)           | (ad-hoc — Phase 2)                   |

### Provenance: structural vs deterministic vs LLM

ADR-012 §4 is unambiguous for **LLM-emitted** edges: no edge without a
`source_reference_id` from the catalog's `source_references` table.
That rule continues to gate every `has_entity` edge — triples without
citations are dropped by the extractor before reaching the projector.

The Demo KG adds two new edge classes that do not fit that pattern:

- **Structural** edges (`has_version`, `has_chunk`, `belongs_to`)
  encode the document/version/chunk/topic skeleton. Their provenance
  is the parent/child relationship itself; demanding a catalog
  `source_reference_id` for "version 3 belongs to document X" is
  meaningless. These follow the `part_of` precedent: no citation
  required.

- **Deterministic semantic** edges (`related_to`, `shares_keyword`,
  `same_topic_as`) come from the chunk-relation service (#141), which
  computes them from chunk-content keyword overlap with no LLM in the
  loop. There is no catalog `source_reference_id` to cite — the
  chunks themselves *are* the provenance. To preserve the spirit of
  ADR-012 §4 (every edge must be auditable), these edges MUST carry a
  parallel audit trail in their `properties`:
  - `source_chunk_ids` — both endpoint chunk ids, repeated as a list
    so consumers can reason about it without parsing source/target
  - `reason` — human-readable string, surfaced verbatim in the
    Orbital node/edge inspector (#151)
  - `shared_keywords` — the overlap that triggered the relation
  - `score` — deterministic similarity in `[0.0, 1.0]`

  See `ChunkRelationEdgeProperties` for the typed shape. A producer
  that emits one of these kinds with empty `source_chunk_ids` or
  empty `reason` is buggy, and the smoke assertions in #146 should
  fail the build for it.

This split is the part most likely to get pushback in review (lane
Blueprint). Calling it out explicitly here so we agree once and don't
relitigate per-PR.

## Property contracts

The `properties` dict is intentionally untyped on the wire so v0.1
producers keep working. Producers SHOULD construct properties through
the typed models below and then flatten with `.model_dump()`:

```python
from app.schemas.knowledge import ChunkNodeProperties, GraphNode

node = GraphNode(
    id=section.id,
    kind="chunk",
    label=section.heading or "Untitled chunk",
    properties=ChunkNodeProperties(
        document_id=document.id,
        version_id=version.id,
        chunk_id=section.id,
        section_id=section.id,
        heading=section.heading,
        text_preview=section.text[:240],
        char_count=len(section.text),
        keywords=keywords,
        topic_id=topic_id,
        source_reference_count=len(section.source_reference_ids),
    ).model_dump(),
)
```

Consumers MAY rehydrate the dict back into the typed model for
validation, but are not required to.

### `ChunkNodeProperties`

| Field                     | Type        | Notes                                                |
| ------------------------- | ----------- | ---------------------------------------------------- |
| `document_id`             | `str`       | required                                             |
| `version_id`              | `str`       | required                                             |
| `chunk_id`                | `str`       | matches `SemanticSection.id` today                   |
| `section_id`              | `str`       | originating section (link back if chunks ever split) |
| `heading`                 | `str?`      | raw section heading                                  |
| `text_preview`            | `str?`      | short preview for the inspector — truncate at source |
| `char_count`              | `int`       | full text length (not preview length)                |
| `keywords`                | `list[str]` | from #141 keyword extraction                         |
| `topic_id`                | `str?`      | set after `project_topics` runs (#142/#144)          |
| `source_reference_count`  | `int`       | from `SemanticSection.source_reference_ids`          |

### `TopicNodeProperties`

| Field          | Type        | Notes                                              |
| -------------- | ----------- | -------------------------------------------------- |
| `document_id`  | `str`       | required                                           |
| `version_id`  | `str`       | required                                           |
| `topic_id`     | `str`       | stable across reprojection (derived from members)  |
| `label`        | `str`       | rendered in the graph and inspector                |
| `keywords`     | `list[str]` | top keywords, ordered by score                     |
| `summary`      | `str?`      | optional one-paragraph summary                     |
| `chunk_count`  | `int`       | convenience for the frontend                       |
| `chunk_ids`    | `list[str]` | members — reverse index of `belongs_to` edges      |

### `ChunkRelationEdgeProperties`

| Field             | Type        | Notes                                                       |
| ----------------- | ----------- | ----------------------------------------------------------- |
| `document_id`     | `str`       | required                                                    |
| `version_id`     | `str`       | required                                                    |
| `source_chunk_id` | `str`       | mirrors edge `source_id`                                    |
| `target_chunk_id` | `str`       | mirrors edge `target_id`                                    |
| `score`           | `float`     | `[0.0, 1.0]` — deterministic similarity                     |
| `reason`          | `str`       | human-readable; surfaced in the inspector                   |
| `shared_keywords` | `list[str]` | overlap that triggered the relation                         |

### `TopicMembershipEdgeProperties`

| Field         | Type    | Notes                                              |
| ------------- | ------- | -------------------------------------------------- |
| `document_id` | `str`   | required                                           |
| `version_id` | `str`   | required                                           |
| `chunk_id`    | `str`   | mirrors edge `source_id`                           |
| `topic_id`    | `str`   | mirrors edge `target_id`                           |
| `score`       | `float` | default `1.0`; missing on consumer ⇒ treat as `1.0` |

## Lane handshakes

- **Lane A (Backend Schema + Projection — #140, #143, #144).** Owns
  this doc and `apps/api/app/schemas/knowledge.py`. Refactors the
  projector into the staged shape from #143. Stages MUST emit the
  property models documented above; deviation is a contract break and
  needs a doc PR.

- **Lane B (Chunk Intelligence — #141, #142).** Imports
  `ChunkNodeProperties`, `TopicNodeProperties`,
  `ChunkRelationEdgeProperties` directly from
  `app.schemas.knowledge`. Does **not** invent parallel dataclasses
  — that path leads to translation layers in the projector. The
  relation service returns a list[`ChunkRelationEdgeProperties`-shaped]
  records; the clustering service returns a
  list[`TopicNodeProperties`-shaped] records plus the chunk → topic
  assignment used to populate `belongs_to` edges.

- **Lane C (Demo Runner + Fixtures + Tests — #145, #146, #147,
  #152).** Smoke assertions in #146 enforce the provenance contract:
  every `related_to` / `shares_keyword` / `same_topic_as` edge
  carries non-empty `source_chunk_ids`, `reason`, and at least one
  shared keyword. Graph artifact export in #145 serializes the v0.2
  payload as-is.

- **Lane D (Frontend KG Visualization — #148, #149, #150, #151).**
  Mirrors the typed property models in
  `apps/web/src/features/graph/types.ts` (or equivalent). Until lane
  A's #144 PR lands, lane D builds against a **mock payload** that
  conforms to this contract. The mock should live alongside the
  graph feature so it can double as fixture for component tests.
  When `belongs_to.score` is missing, treat as `1.0`.

## Open questions

- **Should `section` nodes be removed once `chunk` is in place?**
  Out of scope for #140. Tracked informally — revisit after #144 lands.
- **Topic id derivation.** #142 owns the formula. The contract only
  requires that running the clustering service twice on the same
  input produces the same `topic_id`. Suggested approach:
  `topic-{sha256(sorted(chunk_ids)::label)[:16]}`.
- **Cardinality of `same_topic_as` vs `belongs_to`.** Two chunks in
  the same topic naturally produce both a `belongs_to` (chunk → topic)
  pair and a `same_topic_as` (chunk → chunk) edge. Lane A's #144
  decides whether to emit both for richness or only `same_topic_as`
  when the score adds information beyond shared topic membership.
  Until then, fixture authors can assume both will appear and filters
  in #150 must accommodate that.

## Compatibility checklist (for lane A's #144 PR)

- [ ] `KNOWLEDGE_GRAPH_SCHEMA_VERSION == "v0.2"`.
- [ ] All v0.1 backend tests still pass (`pytest apps/api/tests`).
- [ ] OpenAPI snapshot regenerated
      (`python apps/api/scripts/export_openapi.py`) and committed.
- [ ] `KnowledgeGraphProjection.schema_version` accepts `"v0.1"` AND
      `"v0.2"`, defaults to `"v0.2"`.
- [ ] No edge of kind `related_to` / `shares_keyword` /
      `same_topic_as` ships with empty `source_chunk_ids`,
      `reason`, or `shared_keywords` — assert in tests.
- [ ] `has_entity` edges still carry `source_reference_id` and the
      Phase 2 path remains opt-in.
- [ ] No new dependency on Anthropic or Neo4j for the deterministic
      relation/clustering paths — `KW_KNOWLEDGE_LAYER_ENABLED=false`
      keeps working out of the box.
