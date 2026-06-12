# Customer Knowledge-Graph Demo Runbook

This runbook walks a presenter through the three demo paths for the
KW-Pipeline customer KG demo:

1. **Smoke demo** — no browser, no live server. Drives the full
   upload → extract → semantic → review → graph projection pipeline
   via `TestClient` and writes JSON artifacts you can inspect after
   the fact. Best for CI, pre-flight checks, and "did anything
   regress?" validation.
2. **Live Orbital browser demo** — `kw-demo` API + `npm run dev`
   frontend. The presenter clicks through upload, extract, validate,
   and watches chunks/topics/relations land in the Knowledge Graph
   panel. **No Anthropic, no Neo4j required.**
3. **Enriched demo (optional)** — same live demo plus
   `ANTHROPIC_API_KEY` (entity extraction) and/or `KW_NEO4J_URI`
   (Neo4j-backed graph store). Adds `has_entity` edges and the
   production graph backend.

The hero document for visual graph clarity is
`apps/api/fixtures/customer_demo/acme_quality_program_handbook.txt` —
twelve sections that produce three clean topic clusters
(ISO 9001 / supplier onboarding / customer renewal risk) and ≥ 8
chunk-to-chunk semantic edges.

## Prerequisites

- Python 3.12, `pip install -e 'apps/api[test]'` from the repo root.
- Node 20+ and `npm install` inside `apps/web` for the live demo.
- (Optional) Neo4j 5.23+ and an Anthropic API key for the enriched path.

## Path 1 — smoke demo (no browser)

```bash
make demo-smoke
```

What it does:

- Spins a `TestClient` against `app.main:create_app`.
- Drives the four-fixture customer demo (supplier policy v1/v2,
  customer success brief, contract memo DOCX) plus the new hero
  fixture (`acme_quality_program_handbook.txt`).
- Writes per-version artifacts under
  `.kw-pipeline/customer-demo/artifacts/`:
  - `extraction/<key>.json`
  - `semantic/<key>.needs_review.json`, `semantic/<key>.validated.json`
  - `markdown/<key>.md`
  - **`graph/<key>.json`** (new in #145 — full v0.2 graph projection)
- Aggregates graph counters into
  `.kw-pipeline/customer-demo/artifacts/run_summary.json` under the
  `graph` key (`node_count`, `edge_count`, `chunk_count`,
  `topic_count`, `relation_count`).

Sanity-check after a run:

```bash
jq '.graph' .kw-pipeline/customer-demo/artifacts/run_summary.json
```

Expect a non-zero `chunk_count`, `topic_count ≥ 3`, and
`relation_count ≥ 8` for the hero fixture.

## Path 2 — live Orbital browser demo (no Anthropic, no Neo4j)

Two terminals.

**API:**

```bash
make demo-api
```

This is `kw-demo` under the hood — sets `KW_PERSISTENT=true`,
`KW_KNOWLEDGE_LAYER_ENABLED=true`, the demo content-type allowlist,
and serves on `127.0.0.1:8000` with reload.

**Web:**

```bash
make demo-web
```

This runs `npm run dev` inside `apps/web`, which serves Orbital on
`http://localhost:5173`. CORS is already configured by `kw-demo`.

Presenter script:

1. Open `http://localhost:5173`.
2. Upload `apps/api/fixtures/customer_demo/acme_quality_program_handbook.txt`.
3. Click **Extract** → **Generate Semantic** → **Validate**.
4. Open the **Knowledge Graph** panel — chunks, three topic clusters,
   and chunk-to-chunk semantic relations should render with hover
   reasons (e.g. "Share 5 topic keywords: customer, renewal, risk
   …").

For the duplicate-detection beat, upload the same file again under a
different name; the upload route returns `DUPLICATE_DETECTED` and
extract returns 409.

## Path 2b — the MVP-completion beats (Knowledge Forge)

The MVP-completion sprint (converged plan §C, 2026-06) layered four
operator-facing surfaces on top of the validated document. Run them
in this order after the first upload + validate to show the full
story:

1. **Confidence dashboard** — once a doc lands at `NEEDS_REVIEW`,
   open `/kf/review/<id>?tab=pipeline`. The new **Confidence** card
   between the FSM card and the lifecycle history surfaces the
   composite score, threshold tick, per-signal bars, and the HITL
   routing chip (`routed · auto / human / external`). Talking
   point: *the data has been on disk since EPIC-A slice 1 — what
   was missing was the surface.*
2. **High-value chunks** — same tab, the **High-value chunks** card
   ranks the top 10 chunks by composite importance (claims +
   process steps + graph degree + entity density). Click into one
   to show the per-chunk signal chips. Talking point: *on an 800-
   section document this turns "where do I start?" from minutes
   into seconds.*
3. **Knowledge Explorer** — navigate to `/kf/explore`. Show the
   Atlas landing (top topics + metric cards), then click a topic
   to land on the focused lens (`/kf/explore/topics/<id>`). ADR-028
   "never default to a full-corpus render" is the design point;
   the lens is bounded to depth 2 with a relation-budget cap.
4. **Taxonomy admin** — `/kf/admin/taxonomy?taxonomy_id=…`. Walk
   the lineage list, demote → promote a draft, validate it →
   `VALIDATED_V1`. The Concepts expand panel under a draft row
   shows the Accept / Reject / Defer / Merge actions; the
   `Synthesize` button on a draft calls the LLM creator and folds
   accepted suggestions into the tree.
5. **Roadmap gallery** — `/kf/admin/roadmap`. Five categories of
   intentionally-disabled cards covering everything that the
   converged plan §D defers. Talking point: *every card is greyed
   out by design — this is the post-MVP backlog, not what we ship
   today.*

The same five beats are independent of Path 3 (Anthropic / Neo4j).
None of them require a graph backend to be visible; the Explorer
lens and taxonomy admin work against the in-memory projector by
default.

## Path 3 — enriched demo (optional Anthropic and/or Neo4j)

Both add-ons are independent — turn on either or both.

**Anthropic (entity extraction, `has_entity` edges):**

```bash
export KW_ANTHROPIC_API_KEY="sk-ant-…"
make demo-api
```

When the validate route fires, the projector runs first (chunks /
topics / relations land), then the entity extractor calls Anthropic
once per section and emits `has_entity` edges with
`source_reference_id` citations.

**Neo4j (replace in-memory graph store):**

```bash
make demo-neo4j   # docker compose up -d neo4j (see docker-compose.yml)
export KW_NEO4J_URI="bolt://localhost:7687"
export KW_NEO4J_USER="neo4j"
export KW_NEO4J_PASSWORD="testtesttest"
make demo-api
```

The projector now writes through `Neo4jGraphStore` and reads still
go through the same `/documents/{id}/graph` route — frontend behavior
is identical.

## Where graph artifacts go

- **Smoke run:** `.kw-pipeline/customer-demo/artifacts/graph/<key>.json`,
  one file per validated version. Each file is the v0.2 wire payload
  (`KnowledgeGraphProjection`) — the same JSON the frontend renders.
- **Live API in-memory:** held in process. Restart of `kw-demo`
  clears the graph; persistent SQLite catalog reloads the documents,
  but you must re-validate to re-project (this is by design — the
  graph is a derived projection, not the source of truth).
- **Live API + Neo4j:** persisted in the configured Neo4j database.

## Troubleshooting

- **`/graph` returns empty payload after validate.** Check that
  `KW_KNOWLEDGE_LAYER_ENABLED=true` is set. The default is `false`
  for backwards compatibility with non-graph deployments. `kw-demo`
  and `make demo-smoke` set it for you.
- **Topics don't separate visually.** Make sure you uploaded the
  hero fixture, not one of the shorter supplier policies. The hero
  fixture is engineered with three clean topical clusters; the
  shorter policies have ≤ 2 chunks each and produce a single
  topic.
- **Frontend shows "graph disabled".** The frontend keys off the
  `/health` endpoint's `knowledge_layer_enabled` flag. Restart the
  API after exporting the env var.
- **Anthropic disabled but expected.** `KW_ANTHROPIC_API_KEY` (or
  the legacy unprefixed `ANTHROPIC_API_KEY`) must be non-empty AND
  `KW_KNOWLEDGE_LAYER_ENABLED=true`. Both flags are required.

## See also

- [`docs/roadmap/2026-05-17-converged-knowledge-pipeline-plan.md`](../roadmap/2026-05-17-converged-knowledge-pipeline-plan.md)
  — the MVP scope this runbook closes out (§C.1 confidence
  dashboard, §C.2 high-value chunks, §C.3 roadmap gallery, §C.4
  this runbook), with the §D post-MVP roadmap surfaced through the
  `/kf/admin/roadmap` gallery card.
- [`docs/architecture/knowledge_graph_payload.md`](../architecture/knowledge_graph_payload.md)
  — the v0.2 wire contract that the runbook above demonstrates.
- [`docs/adr/ADR-012-knowledge-graph-layer.md`](../adr/ADR-012-knowledge-graph-layer.md)
  — projection design, source-reference invariant, Neo4j vs in-memory
  store tradeoffs.
- [`docs/adr/ADR-013-llm-provider-and-no-langchain.md`](../adr/ADR-013-llm-provider-and-no-langchain.md)
  — why entity extraction calls Anthropic directly, no LangChain.
- [`docs/adr/ADR-018-taxonomy-versioning-lifecycle.md`](../adr/ADR-018-taxonomy-versioning-lifecycle.md)
  — the DRAFT → CANDIDATE_V0 → VALIDATED_V1 → ARCHIVED state
  machine the `/kf/admin/taxonomy` beat drives.
- [`docs/adr/ADR-023-hitl-routing-and-spc-sampling.md`](../adr/ADR-023-hitl-routing-and-spc-sampling.md)
  — the confidence scorer the §C.1 panel reads from.
- [`docs/adr/ADR-028-kw-explorer-large-corpus-ux.md`](../adr/ADR-028-kw-explorer-large-corpus-ux.md)
  — the focused-lens contract the Explorer beat demonstrates.
