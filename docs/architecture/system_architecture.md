# KW Pipeline — System Architecture

This document is the canonical, single-source overview of what KW
Pipeline is, what it does, what runs where, and which technology
choices back each layer. It rolls together the 13 ADRs, the per-area
architecture docs, and the current state of the codebase as of
2026-05-04.

It is organised so that each section can feed an architecture diagram
generator (Mermaid / Structurizr / C4 / draw.io) directly.

---

## 1. Product summary

### 1.1 What KW Pipeline does

KW Pipeline is a **document intelligence platform** that turns raw
business documents into a **governed, source-backed knowledge graph**
that can be searched, navigated, and queried by humans and LLMs.

Five things must always be true of any output it produces:

1. Every document is **immutable** (SHA-256 of the bytes is the
   identity).
2. Every extraction is **versioned** (re-uploads create v2, v3… in the
   same family).
3. Every semantic claim has **lineage** back to a source span.
4. Every output is **reviewable** before it is trusted (`NEEDS_REVIEW`
   → `VALIDATED` / `REJECTED`).
5. Nothing without **provenance** ever lands in the knowledge graph.

### 1.2 The four user-facing surfaces

| Surface | Audience | Mode | Stack |
|---|---|---|---|
| `apps/api` | services + UIs | HTTP API, FastAPI | Python 3.11+ |
| `apps/web` (Orbital) | internal reviewers | full-window SPA | Vite + React 19 |
| `apps/widget` (KnowledgeForge) | 3DEXPERIENCE dashboard users | embedded 3DX widget | Webpack + React 19 |
| `apps/explorer` (Knowledge Explorer) | 3DEXPERIENCE dashboard users | embedded 3DX widget — **read-only** | Webpack + React 19 |
| `apps/widget-preview` | widget developers | browser dev harness | Vite + stub |

The widget surface is intentionally split in two:

- **KnowledgeForge widget (`apps/widget`)** is the **operate** surface
  — upload, status, ingest, ask. It writes to the API.
- **Knowledge Explorer (`apps/explorer`)** is the **navigate** surface
  — browse the validated corpus, follow concepts, inspect chunks. It
  is read-only by design.

The reviewer workbench (`apps/web`) is the **govern** surface — the
human gate that turns `NEEDS_REVIEW` into `VALIDATED`.

### 1.3 The product story in one diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│  3DEXPERIENCE Dashboard (host)                                        │
│                                                                       │
│   ┌──────────────────────┐         ┌─────────────────────────────┐    │
│   │ KnowledgeForge       │  ─→ ←─  │ Knowledge Explorer          │    │
│   │ widget (operate)     │         │ widget (navigate, read-only)│    │
│   │ apps/widget          │         │ apps/explorer               │    │
│   └─────────┬────────────┘         └─────────────┬───────────────┘    │
│             │                                     │                    │
└─────────────┼─────────────────────────────────────┼────────────────────┘
              │ HTTP/JSON                           │ HTTP/JSON
              │                                     │
              ▼                                     ▼
        ┌──────────────────────────────────────────────────┐
        │  KW Pipeline API  (apps/api)                      │
        │  FastAPI · Python · Pydantic · SQLite · Anthropic │
        └─────────────────────┬────────────────────────────┘
                              │
              ┌───────────────┴───────────────┐
              │                               │
              ▼                               ▼
        ┌──────────────┐               ┌──────────────────────┐
        │ Reviewer     │               │ Optional             │
        │ workbench    │               │ Knowledge Layer      │
        │ apps/web     │               │  • Neo4j 5.x graph   │
        │ (govern)     │               │  • Voyage embeddings │
        └──────────────┘               │  • Anthropic Claude  │
                                       └──────────────────────┘
```

---

## 2. Backend — `apps/api` (codename **Harvester**)

### 2.1 Tech stack at a glance

| Concern | Choice | Why | ADR |
|---|---|---|---|
| HTTP framework | FastAPI | typed routes, OpenAPI for free | implied by ADR-011 |
| Validation / models | Pydantic 2 (`APISchemaModel` base) | required-list defaults flip via override | ADR-008 |
| Catalog database | SQLite (file-backed) behind `CatalogStore` Protocol | MVP simplicity, Postgres path open | persistence.md |
| Object storage | local filesystem under `.kw-pipeline/raw/` behind `StorageService` | swap to S3/MinIO later | persistence.md |
| Graph database (optional) | Neo4j 5.x Community via Docker compose, behind `GraphStore` Protocol | ADR-012 picks Neo4j; in-memory fake by default | ADR-012 |
| LLM provider (optional) | Anthropic Claude via `anthropic` SDK, behind `LLMClient` Protocol | ADR-013: Anthropic only, no LangChain | ADR-013 |
| Embeddings (optional) | Voyage AI (`voyage-3`), behind `EmbeddingClient` Protocol | ADR-015 picks Voyage | ADR-015 |
| PDF parser | `pdfplumber` | cold-start + license; Docling rejected at MVP | ADR-010 |
| DOCX parser | `python-docx` | de-facto standard | implied |
| PPTX parser | `python-pptx` | de-facto standard | implied |
| Configuration | `pydantic-settings` (`Settings(BaseSettings)`) | every env var typed | issue #43 |
| Structured logging | stdlib + JSON formatter (`KW_LOG_FORMAT=json`) | event-vocabulary doc | logging.md, observability.md |
| Audit events | append-only `audit_events` table (`audit_event_store.py`) | persisted alongside logs | issue #26 / PR #206 |
| OpenAPI codegen | snapshot dump + `openapi-typescript` consumer | ADR-011 | ADR-011 |
| Type checking | `mypy` strict on `apps/api/app` | CI gate | issue #44 |
| Lint / format | `ruff check` + `ruff format` | pre-commit + CI | — |
| Testing | `pytest`, `hypothesis` (planned), `pytest -m integration` for Neo4j | 95% coverage gate | — |

### 2.2 Module layout

```
apps/api/app/
├── main.py                    # FastAPI factory (create_app, persistent flag)
├── settings.py                # Pydantic Settings — every env var
├── routes.py                  # Single router; mounted by main.py
├── dependencies.py            # FastAPI dependency wiring (PipelineServices)
├── errors.py                  # Custom error envelope + handlers
├── logging_config.py          # Structured logging + audit handler
├── demo.py                    # kw-demo console entry
├── models/
│   └── document.py            # Lifecycle FSM (DocumentStatus enum + transitions)
├── schemas/
│   ├── document.py            # Document, DocumentVersion, BatchUploadResult
│   ├── semantic_document.py   # SemanticDocument, SemanticSection, SemanticAsset
│   └── knowledge.py           # KG nodes, edges, chat / search models
└── services/
    ├── audit_event_store.py   # Append-only audit trail
    ├── audit_log_handler.py   # Logger adapter that writes to the store
    ├── catalog_store.py       # CatalogStore Protocol + SQLite + in-memory
    ├── document_service.py    # Upload, family lineage, dup detection
    ├── document_parser.py     # Parser registry (PARSERS dict)
    ├── extraction_job_service.py  # Per-version extraction orchestration
    ├── hash_service.py        # Streaming SHA-256
    ├── idempotency_store.py   # Idempotency-Key cache
    ├── markdown_generator.py  # Jinja2 template → semantic Markdown
    ├── migrations.py          # Versioned schema migration runner
    ├── parsers/
    │   ├── pdf.py             # pdfplumber adapter
    │   ├── docx.py            # python-docx adapter
    │   └── pptx.py            # python-pptx adapter
    ├── enrichers/
    │   ├── rule_based_entities.py  # Deterministic enricher
    │   └── spacy_ner.py            # Opt-in spaCy NER (#190)
    ├── semantic_extractor.py  # Calls enrichers + LLM enricher boundary
    ├── semantic_output_service.py  # Persists semantic JSON + Markdown
    ├── semantic_schema_loader.py   # ADR-008 versioned loader
    ├── storage_service.py     # Filesystem object store
    └── knowledge/             # Knowledge Layer (opt-in)
        ├── graph_store.py     # GraphStore Protocol + Neo4j + in-mem
        ├── projector.py       # Stages: structure, chunks, relations, topics, entities
        ├── chunk_relations.py # Deterministic chunk-to-chunk edges
        ├── topic_clustering.py# Connected-components topic builder
        ├── entity_extractor.py# LLM tool-use entity extraction
        ├── llm_client.py      # LLMClient Protocol + Anthropic + Fake
        ├── embedding_client.py# EmbeddingClient Protocol + Voyage + Fake
        ├── search.py          # GET /knowledge/search (vector, in flight)
        ├── chat_service.py    # POST /knowledge/chat (RAG / Graph / Hybrid)
        └── reconciliation.py  # Catch-up reprojector for missed events
```

### 2.3 Domain model

```text
Document  (immutable identity per family)
 │   id, filename, sha256, owner?, source metadata?
 │
 ├── DocumentVersion  (immutable bytes, mutable lifecycle status)
 │     id, version_number, sha256, status (FSM), failure_reason?,
 │     duplicate_of_version_id?, parser_name?, schema_version, …
 │
 │     ├── RawExtraction  (parser output, opaque sections list)
 │     │
 │     ├── SemanticDocument  (schema-validated)
 │     │     ├── SemanticSection[]   ← chunk seed (id, heading, text, source_refs)
 │     │     │     └── SemanticAsset[] ← typed claims (concept, requirement, …)
 │     │     └── SourceReference[]
 │     │
 │     └── Markdown  (one .md per version; Jinja2 template)
 │
 └── AuditEvent[]  (append-only)
```

In the Knowledge Layer (opt-in), every `VALIDATED` `DocumentVersion`
projects into Neo4j as:

```text
(:Document)-[:HAS_VERSION]->(:Version)-[:HAS_SECTION]->(:Section)
                                       -[:HAS_CHUNK]->(:Chunk)
                                                       -[:BELONGS_TO]->(:Topic)
                                                       -[:RELATED_TO]->(:Chunk)         (deterministic semantic edge)
                                                       -[:SHARES_KEYWORD]->(:Chunk)
                                                       -[:SAME_TOPIC_AS]->(:Chunk)
(:Section)-[:HAS_ENTITY]->(:Entity)                                                     (LLM-extracted, opt-in Phase 2)
```

Every edge carries a `source_reference_id`. No exception.

### 2.4 Lifecycle FSM

States (declared in `apps/api/app/models/document.py`):

```
UPLOADED
  └─ HASHED
       ├─ DUPLICATE_DETECTED  (terminal for this version)
       └─ STORED
            └─ QUEUED_FOR_EXTRACTION
                 └─ EXTRACTING
                      ├─ FAILED  (terminal, with failure_reason)
                      └─ EXTRACTED
                           └─ NEEDS_REVIEW
                                ├─ REJECTED   (terminal)
                                └─ VALIDATED  (triggers KG side-effect)
```

Properties enforced server-side:

- The transition is enforced by `update_version_status` with a SQL
  `WHERE current_status = ?` predicate (optimistic concurrency).
- Validation never rolls back on a knowledge-layer error: the graph
  projection runs as a fire-and-log side-effect of `mark_validated`.
- Re-uploads append a new version inside the **same family** (today,
  duplicate-without-`document_id` creates a new family — tracked as
  bug #59).

### 2.5 Pipeline stages

| Stage | Module | Output | Lifecycle delta |
|---|---|---|---|
| Upload | `document_service._upload_*` | DocumentVersion + raw blob | `UPLOADED → HASHED → STORED` (or `DUPLICATE_DETECTED`) |
| Extraction | `extraction_job_service.run` → `document_parser.PARSERS[content_type]` | RawExtraction (sections list) | `STORED → QUEUED_FOR_EXTRACTION → EXTRACTING → EXTRACTED` (or `FAILED`) |
| Semantic generation | `semantic_extractor.SemanticExtractor.run` | SemanticDocument (schema-valid) | `EXTRACTED → NEEDS_REVIEW` |
| Markdown rendering | `markdown_generator.render` | one `.md` per version | side-effect of semantic generation |
| Review | `POST /validate` or `POST /reject` | reviewer note + actor (when auth lands) | `NEEDS_REVIEW → VALIDATED` or `REJECTED` |
| Graph projection (opt-in) | `knowledge.projector.KnowledgeProjector` | (:Document)…(:Section) + chunks + topics + relations | side-effect of `VALIDATED` |
| Entity extraction (opt-in) | `knowledge.entity_extractor.EntityExtractor` | (:Entity) nodes with citations | side-effect after projection |
| Vector indexing (in flight) | `knowledge.embedding_client` + `projector.project_chunks` | Voyage embeddings on `(:Chunk).embedding` | side-effect of projection |

### 2.6 Storage layers

```
.kw-pipeline/                          (gitignored, persistent mode)
├── catalog.sqlite3                    Catalog + audit_events
├── raw/<sha256>                       Raw uploaded bytes
├── extractions/<version_id>.json      RawExtraction
├── semantic/<version_id>.json         SemanticDocument
└── markdown/<version_id>.md           Generated Markdown

Neo4j (opt-in, container)              Knowledge graph
└── bolt://localhost:7687              Documents/Versions/Sections/Chunks/Topics/Entities
```

### 2.7 LLM, embedding, and graph integration

The three optional integrations are gated by env vars. Every one is
behind a Python `Protocol`, with a `Fake*` implementation used in
tests.

```python
class GraphStore(Protocol):
    def upsert_node(self, node: GraphNode) -> None: ...
    def upsert_edge(self, edge: GraphEdge) -> None: ...
    def index_chunk_embeddings(self, chunks: list[ChunkEmbedding]) -> None: ...

class LLMClient(Protocol):
    def extract_entities(self, *, text: str, citations: list[str]) -> list[Triple]: ...
    def complete_text(self, *, system: str, user: str) -> str: ...

class EmbeddingClient(Protocol):
    def embed(self, texts: list[str], *, model: str) -> list[list[float]]: ...
```

Everything is wired through `dependencies.py::PipelineServices`,
which is constructed once per app and passed into route handlers.

### 2.8 API surface

Public routes (mounted at `/`):

```
GET    /health
POST   /documents/upload                                  upload one
POST   /documents/upload/batch                            multipart batch
GET    /documents                                         cursor-paginated list
GET    /documents/{id}                                    family + versions
POST   /documents/{id}/versions/{vid}/extract             trigger extraction
POST   /documents/{id}/versions/{vid}/retry               re-trigger after FAILED
GET    /documents/{id}/versions/{vid}/extraction          raw extraction
POST   /documents/{id}/versions/{vid}/semantic            trigger semantic gen
GET    /documents/{id}/versions/{vid}/semantic            semantic JSON
GET    /documents/{id}/versions/{vid}/markdown            generated Markdown
GET    /documents/{id}/versions/{vid}/raw                 raw bytes
POST   /documents/{id}/versions/{vid}/validate            reviewer accepts
POST   /documents/{id}/versions/{vid}/reject              reviewer rejects

# Knowledge layer (opt-in)
GET    /documents/{id}/graph                              one-doc projection
GET    /knowledge/graph                                   cross-doc, paginated
GET    /knowledge/search                                  top-K vector chunks (in flight)
POST   /knowledge/chat                                    RAG / Graph / Hybrid (mode in body, ADR-016)
```

Every route carries an `operation_id` so the typed TypeScript client
exposes a stable name (ADR-011). The OpenAPI snapshot in
`apps/api/openapi.json` is the contract; `apps/web` regenerates
`schema.ts` from it as a CI gate.

### 2.9 Configuration matrix

Every knob is an environment variable read by
`apps/api/app/settings.py::Settings`. The most material ones:

| Var | Default | Effect |
|---|---|---|
| `KW_PERSISTENT` | `false` | When `true`, swaps in-memory services for SQLite + filesystem |
| `KW_CORS_ALLOWED_ORIGINS` | `[]` | Comma-separated origin allowlist |
| `KW_ALLOWED_CONTENT_TYPES` | text/plain only | Content-type allowlist on upload |
| `KW_LOG_FORMAT` | `text` | `json` produces structured logs |
| `KW_KNOWLEDGE_LAYER_ENABLED` | `false` | Master kill-switch for the KG layer |
| `KW_NEO4J_URI` / `_USER` / `_PASSWORD` / `_DATABASE` | unset | Bolt connection; falls back to in-memory fake when unset |
| `ANTHROPIC_API_KEY` | unset | Phase 2 entity extraction + Phase 3 chat |
| `KW_LLM_MODEL` | `claude-sonnet-4-5` | Claude model id |
| `VOYAGE_API_KEY` | unset | Phase 3 vector embeddings |
| `KW_EMBEDDING_MODEL` | `voyage-3` | Voyage model id |
| `KW_NER_ENABLED` | `false` | Opt-in spaCy NER enricher (#190) |
| `KW_CHAT_DISABLED` | derived | 503 envelope when chat gate is off |

### 2.10 Audit and observability

- **Logging.** Every business action emits a structured event
  (`document.uploaded`, `version.extracted`, `version.validated`, …)
  through `logging_config.py`. Format toggled by `KW_LOG_FORMAT`.
- **Audit store.** The same handler tees events into the
  `audit_events` table (`audit_event_store.py`, shipped 2026-05-04 in
  PR #206). Every event records `actor` (placeholder until auth lands),
  `subject_id`, `event_type`, `payload`, `timestamp`.
- **Health.** `GET /health` is a liveness probe. Readiness + metrics
  are tracked in #96 and not yet built.

---

## 3. Frontend — three apps, one API

### 3.1 Why three frontends and not one

The three apps **share an API** but **diverge on host, identity,
and intent**:

| App | Host | Bundler | Intent | Writes? |
|---|---|---|---|---|
| `apps/web` (Orbital) | standalone browser | Vite | reviewer **governance** workbench | yes (validate/reject) |
| `apps/widget` (KnowledgeForge) | 3DEXPERIENCE 3DDashboard tile | Webpack | corpus **operate** (upload, status, ask) | yes (upload, chat) |
| `apps/explorer` (Knowledge Explorer) | 3DEXPERIENCE 3DDashboard tile | Webpack | validated-corpus **navigate** (read-only) | no |

The two 3DX widgets share the dependency on
`@widget-lab/3ddashboard-utils` (file-linked from a developer-side
clone of the Dassault Systèmes utility package); the standalone
`apps/web` does not.

A fourth folder, `apps/widget-preview`, is a Vite shell that mounts
the real `apps/widget/src/App` in a browser tab without 3DX, by
stubbing the `@widget-lab` runtime. It exists to keep widget
development possible without 3DEXPERIENCE credentials.

### 3.2 `apps/web` — Orbital reviewer workbench

**Purpose.** Internal reviewer UI. The human gate that drives
documents from `NEEDS_REVIEW` to `VALIDATED` or `REJECTED`.

**Stack.** Vite 6 + React 19 + React-Router 6 + TypeScript 5 +
Vitest + Testing Library + axe-core. The typed API client comes from
`openapi-fetch` against the schema generated by `openapi-typescript`
from the FastAPI snapshot.

**Routing & feature shape.**

```
apps/web/src/
├── App.tsx                       # Route shell
├── main.tsx                      # ReactDOM.createRoot + provider
├── api/
│   ├── client.ts                 # openapi-fetch + base URL + auth (TODO)
│   ├── types.ts                  # Re-exports from generated/
│   └── generated/schema.ts       # ← regenerated by `npm run openapi:generate`
├── domain/document.ts            # Frontend-side domain types & helpers
├── ui/StatusBadge.tsx            # Lifecycle status pill
└── features/
    ├── pipeline/PipelineWidget.tsx     # Compact upload + catalog + status
    ├── review/
    │   ├── ReviewWorkspace.tsx         # Markdown preview + side panel + actions
    │   └── ReviewActions.tsx           # validate / reject buttons
    ├── graph/
    │   ├── KnowledgeGraphView.tsx      # @neo4j-nvl/react graph
    │   ├── types.ts                    # GraphNode / GraphEdge frontend types
    │   └── __mocks__/v0_2_payload.ts   # Test mock
    ├── search/SearchPanel.tsx          # GET /knowledge/search panel
    └── chat/
        ├── ChatPanel.tsx               # POST /knowledge/chat
        └── ChatModeToggle.tsx          # rag / graph / hybrid
```

**Bundle strategy.** Initial bundle is ~70 KB gzipped. The
NVL-based graph view is **lazy-split** — `@neo4j-nvl/base` only
loads when the panel mounts (~510 KB chunk). Bundle budgets are
enforced by `npm run bundle:check` and CI.

**Patterns.**

- Every API call goes through the generated client; no untyped
  fetch.
- Every action that mutates state (validate, reject, upload, retry,
  semantic, extract) carries an `Idempotency-Key` header; the
  backend rejects 422 on body mismatch.
- The graph view consumes the `KnowledgeGraphProjection` schema
  directly; unknown node/edge kinds degrade safely.

### 3.3 `apps/widget` — KnowledgeForge 3DEXPERIENCE widget

**Purpose.** Inside the 3DEXPERIENCE 3DDashboard, this widget is the
**operate** surface — upload documents, watch the pipeline progress,
ask the corpus questions. Today it does **not** carry the reviewer
governance UI; that lives in `apps/web` for internal use.

**Stack.** Webpack 5 + Babel + React 19 + TypeScript 5 +
`@widget-lab/3ddashboard-utils` (file-linked from the developer's
clone). No Vite — the 3DX dashboard expects a single bundled JS
artifact suitable for the host's loader.

**Layout.**

```
apps/widget/src/
├── App.tsx                       # Tile shell (header + side rail + sections)
├── index.tsx                     # 3DDashboard entry point
├── components/
│   ├── Header.tsx                # Brand + connection state
│   ├── SideRail.tsx              # Section switcher
│   ├── StatusBadge.tsx
│   ├── FileTypeIcon.tsx
│   ├── EmptyState.tsx
│   ├── SectionHeader.tsx
│   └── icons.tsx
├── sections/
│   ├── UploadQueue.tsx           # Drag/drop + per-file outcome list
│   ├── DocumentsList.tsx         # Catalog browser with filters
│   ├── KnowledgeSummary.tsx      # Counts (validated, pending, failed)
│   ├── HealthCard.tsx            # GET /health probe
│   ├── SearchPanel.tsx           # GET /knowledge/search inside the tile
│   └── ChatPanel.tsx             # POST /knowledge/chat (in flight)
├── settings/SettingsPanel.tsx    # API base URL + creds
└── api/{client.ts,types.ts}      # Hand-curated client (no openapi-fetch — webpack/babel chain)
```

**Why a different bundler.** The 3DEXPERIENCE host expects a UMD-ish
bundle that integrates with the dashboard's runtime conventions and
loads `@widget-lab/3ddashboard-utils` from a file path inside the
developer's home (`~/.kw-pipeline/3ddashboard-utils/`). Webpack +
Babel match the official `@widget-lab` template; Vite does not.

### 3.4 `apps/explorer` — Knowledge Explorer 3DEXPERIENCE widget

**Purpose.** A **read-only navigation** tile for the validated
corpus. It is the 3DX answer to "I want to walk through everything
KW Pipeline has captured, see how documents cluster into topics,
follow concepts across documents, and read the original source for
any chunk."

**Why separate from `apps/widget`.** `apps/widget` writes to the
API (uploads, asks). `apps/explorer` only **reads**. Splitting them:

- keeps the read-only tile shippable without `Idempotency-Key`,
  upload UI, or chat write-paths;
- reduces the auth-blast-radius once auth lands (Explorer is
  viewer-only; KnowledgeForge is contributor/admin);
- lets each tile own one mental model (the design language is
  intentionally different — Explorer is wide-canvas exploratory,
  KnowledgeForge is narrow-tile operational).

**Stack.** Same as `apps/widget` (Webpack 5 + Babel + React 19 +
`@widget-lab/3ddashboard-utils`). Same `~/.kw-pipeline/3ddashboard-utils/`
file-link.

**Layout.**

```
apps/explorer/src/
├── App.tsx                       # Three-column shell (hierarchy / canvas / viewer+detail)
├── index.tsx                     # 3DDashboard entry point
├── components/
│   ├── GraphCanvas.tsx           # Custom Cytoscape-backed graph view
│   ├── DocViewer.tsx             # Original document preview with chunk highlight
│   ├── DetailPanel.tsx           # Selected node properties + actions
│   └── icons.tsx
├── api/{client.ts,types.ts}      # Read-only API helpers (catalog + graph)
└── state/
    ├── explorer-data.ts          # ExplorerSnapshot schema + helpers
    └── use-explorer-data.ts      # Live snapshot loader + sample-fallback
```

**UI model — three columns.**

```
┌─────────────────────────────────────────────────────────────────────────┐
│ HEADER  brand · view tabs (Corpus Overview / Concept Map) · search · ⚙ │
├──────────────┬───────────────────────────────────┬──────────────────────┤
│ HIERARCHY    │  GRAPH CANVAS                     │  DOC VIEWER          │
│ • Corpus     │  • Cluster halos                  │  - Original doc      │
│ • Filters    │  • Document nodes (typed)         │  - Chunk highlight   │
│ • Legend     │  • Chunk nodes                    │                      │
│ • Depth      │  • Concept nodes                  │  DETAIL PANEL        │
│              │  • Browser-style nav (back/fwd/⌂) │  - Selected node     │
│              │                                   │  - Evidence links    │
└──────────────┴───────────────────────────────────┴──────────────────────┘
```

**Two views.**

- **Corpus Overview.** Hierarchy: cluster → document → chunk.
  Concepts overlay. Default first run.
- **Concept Map.** Concept-centric. Node = concept; edges go to
  every chunk that mentions it. Lets the user follow a topic across
  multiple documents.

**Live data + sample fallback.** `useExplorerData` calls the live
KW Pipeline API (`GET /knowledge/graph` + catalog list) and shapes
it into an `ExplorerSnapshot`. If the API is unreachable or returns
nothing, the hook falls back to a baked-in sample corpus so the
widget is always demoable. The fallback is annotated in the UI
("Sample data — backend unreachable").

**Read-only by design.** Every interaction is navigational: click,
focus, expand, follow, search, deep-link via URL hash (`#doc/d4`,
`#concept/k2`, `#chunk/c4.1`). There is **no** validate / reject /
upload action. A "READ-ONLY" pill is shown on the canvas to make
the boundary explicit.

**Key state primitives in the shell** (see `apps/explorer/src/App.tsx`):

```text
view             corpus | concepts            top-level mode
selected         NodeSelection | null         current selection (drives DetailPanel)
openDocId        string | null                drives DocViewer
highlightChunk   string | null                drives chunk highlight in DocViewer
expandedClusters Set<string>                  hierarchy state
expandedDocs     Set<string>                  hierarchy state
focusRoot        FocusRoot | null             "zoom to this node, depth N" focus stack
history/forward  FocusRoot[]                  browser-style nav stack
filters          {types, sources}             corpus filters
tweaks           {theme, density, layoutMode, …}  UI options (overlay panel)
```

### 3.5 `apps/widget-preview` — dev shell

A Vite + React harness whose only job is to mount the real
`apps/widget/src/App` inside a browser tab so widget development
can happen **without** a 3DEXPERIENCE host.

It does this by providing `widget-stub.ts` — a tiny stub of
`@widget-lab/3ddashboard-utils` — and resolving the import to the
stub via Vite's module aliasing. Hot reload is live; an edit to
`apps/widget/src/` shows up in ~200 ms.

This shell is **not** a deployable artifact — it has no place in
production. It is purely a developer convenience. The same pattern
is used by `./scripts/demo-frontend.sh`.

### 3.6 Shared frontend concerns

**Typed API client.** Only `apps/web` consumes the generated
`schema.ts` directly via `openapi-fetch`. The two 3DX widgets
maintain hand-curated `api/types.ts` because their Webpack/Babel
toolchain pre-dates the openapi-fetch generation pipeline; this is a
cleanup candidate (could be added without rewriting them).

**Theming.** Each app owns its own design tokens today. The widget
brand-token adapter (#78) is the planned consolidation point —
needed before a real 3DEXPERIENCE deployment.

**Auth.** Today there is none. Once #83 lands, the auth model
(decision D1 — 3DX SSO vs API tokens vs OIDC) determines how each
frontend acquires identity. The 3DX widgets are expected to inherit
identity from the dashboard host; `apps/web` will need its own
sign-in path.

---

## 4. End-to-end user flow

### 4.1 The reviewer flow (apps/web + apps/api)

```
┌─ user uploads a document ─────────────────┐
│  apps/web/PipelineWidget                  │
│  → POST /documents/upload                 │
└──────────────────┬─────────────────────────┘
                   ▼
          DocumentService._upload_new_family
          • streaming SHA-256
          • content-type allowlist check
          • dup detection by hash
          → DocumentVersion(status=STORED)
                   │
                   ▼ (kicked off automatically or manually)
          extraction_job_service.run
          • picks parser by content_type
          • PARSERS["application/pdf"] = pdfplumber adapter
          → RawExtraction
          → DocumentVersion(status=EXTRACTED)
                   │
                   ▼
          semantic_extractor.SemanticExtractor.run
          • rule-based enricher
          • optional spaCy NER (KW_NER_ENABLED)
          • optional LLM enricher (ADR-009 boundary; needs_review by default)
          → SemanticDocument (schema-validated)
          → markdown_generator.render → file
          → DocumentVersion(status=NEEDS_REVIEW)
                   │
                   ▼
          apps/web/ReviewWorkspace
          • renders Markdown preview, source spans, asset list
          • reviewer clicks Validate or Reject
          → POST /validate or /reject (Idempotency-Key)
          → DocumentVersion(status=VALIDATED | REJECTED)
                   │
                   ▼ (only when KW_KNOWLEDGE_LAYER_ENABLED)
          knowledge.projector.KnowledgeProjector.project
          • project_document_structure → (:Document)…(:Section)
          • project_chunks → (:Chunk)
          • project_chunk_relations → :RELATED_TO / :SHARES_KEYWORD
          • project_topics → (:Topic) and :BELONGS_TO / :SAME_TOPIC_AS
          • project_entities (Phase 2, when ANTHROPIC_API_KEY set)
                          → (:Entity) with citations
          → fire-and-log; validation never rolls back
```

### 4.2 The navigate flow (apps/explorer + apps/api)

```
apps/explorer mounts in 3DDashboard tile
   ├─ useExplorerData
   │   GET /knowledge/graph   (cross-doc projection, paginated)
   │   GET /documents          (catalog list, for cluster/source labels)
   │   → ExplorerSnapshot { documents, chunks, concepts, edges, … }
   │
   ▼
GraphCanvas renders Corpus Overview
   ├─ Hierarchy column drives expand/collapse + cluster halos
   ├─ Canvas: click any node → onSelect(NodeSelection)
   ├─ Selection drives DocViewer (open original doc, highlight chunk)
   ├─ Selection drives DetailPanel (properties + evidence + actions)
   ├─ Search → typeahead over docs/chunks/concepts
   ├─ Focus stack (back/forward/home) for browser-style navigation
   └─ URL hash deep-link (#doc/<id> | #chunk/<id> | #concept/<id>)
```

The two flows share the API but never cross client-side: a reviewer
in `apps/web` does not see the navigation tile, and an explorer user
does not see review actions. The shared substrate is the validated
graph in the API + Neo4j.

### 4.3 The operate flow (apps/widget + apps/api)

```
apps/widget mounts in 3DDashboard tile
   ├─ HealthCard → GET /health
   ├─ DocumentsList → GET /documents
   ├─ UploadQueue → POST /documents/upload (multi-select; batch via /upload/batch)
   ├─ KnowledgeSummary → counts of validated / pending / failed
   ├─ SearchPanel → GET /knowledge/search (chunks)
   └─ ChatPanel → POST /knowledge/chat  (mode: rag | graph | hybrid)
```

This is the **contributor** surface. Validation still happens in
`apps/web`; the widget is intentionally thin on governance UI.

---

## 5. Architectural decisions — synthesis

### 5.1 The 13 ADRs in one table

| # | Title | One-line decision | Status |
|---|---|---|---|
| 001 | Document Intelligence MVP | Pipeline of immutable versions, schema-validated semantic JSON gated by `NEEDS_REVIEW` | accepted |
| 002 | Hash + versioning + dedup | SHA-256 of bytes is the duplicate key | accepted |
| 003 | Semantic Markdown output | One Markdown per version with YAML frontmatter | accepted |
| 004 | Orbital frontend stack | Vite + React + TS for the reviewer SPA; reject Next/Electron/Tauri | accepted |
| 008 | Schema versioning | `vMAJOR.MINOR` literal + central loader + per-version migrators + fixtures | accepted |
| 009 | SemanticEnricher boundary | LLM enrichers go through a Protocol; outputs forced to `needs_review`; exception-isolated | accepted |
| 010 | PDF parser | `pdfplumber` for the MVP; Docling rejected (cold-start + license); revisit later | accepted |
| 011 | OpenAPI codegen | Backend dumps `openapi.json`; frontend regenerates `schema.ts`; CI gate on drift | accepted |
| 012 | Knowledge graph layer | Neo4j Community via Docker behind `GraphStore` Protocol; project on `VALIDATED` only | accepted |
| 013 | LLM provider | Anthropic only via official SDK behind `LLMClient` Protocol; **no LangChain anywhere** | accepted |
| 014 | Entity extraction | Per-section tool-use prompt, citation-required, ephemeral cache, retry, per-doc token cap | accepted |
| 015 | Embedding provider | Voyage AI (`voyage-3`) behind `EmbeddingClient` Protocol; Neo4j vector index | accepted |
| 016 | Chat surface mode taxonomy | Single `POST /knowledge/chat`, mode discriminator in the body | accepted |

ADR slots **005**, **006**, **007** are unused. ADR-006 was
*reserved* by issue #40 (async extraction queue) but never written;
this is the most material missing ADR.

### 5.2 Cross-cutting principles enforced by ADRs

- **Provenance is mandatory.** No graph edge without
  `source_reference_id`. No semantic claim without a section
  citation. The Phase 2 LLM extractor drops triples that violate
  this into a `warnings` array.
- **LLM outputs are never trusted.** Everything from an LLM lands as
  `needs_review`, is re-validated against Pydantic schemas, and is
  discarded if it fails (ADR-009).
- **Optional integrations are gated.** Neo4j, Anthropic, Voyage are
  all opt-in via env vars and behind a `Protocol`. The `Fake*`
  adapters keep `pytest` runnable on a laptop without Docker or a
  network LLM.
- **Side-effects never roll back the catalog.** Knowledge-layer
  projection runs *after* `mark_validated`; any failure logs and
  exits.
- **No LangChain.** Patterns we want from `llm-graph-builder`
  (Apache-2) are vendored as auditable Python (ADR-013).
- **Stable API contract.** Every route has an `operation_id`; the
  TypeScript client is regenerated from the snapshot in CI.

### 5.3 What is decided but undocumented

A few decisions effectively exist in code but never landed in an
ADR. They are easy wins to formalise:

- **Persisted audit events** (PR #206) — extension of #42, but no
  ADR covers retention, tamper-evidence, or query surface.
- **Deterministic chunk relations + topic clustering** (issues
  #141, #142) — no ADR covers the algorithm choice.
- **Idempotency-Key on POST endpoints** — wired everywhere, no ADR.
- **Optimistic concurrency on lifecycle transitions** — done via
  `WHERE current_status = ?`, no ADR.

### 5.4 What is undecided and blocking

See `docs/roadmap/2026-05-04-backlog-restructure.md` §A.4 for the
full 14-row decision matrix. The most material:

- **D1 — Auth model** (3DX SSO / API tokens / OIDC).
- **D2 — Workspace boundary** (project / 3DX collab space / tenant).
- **D5 — Async queue technology** (`#40`, `ADR-006`).
- **D11 — Persistence trajectory** (SQLite → Postgres path).
- **D12/D13 — Taxonomy persistence + LLM strategy** (EPIC 1).

---

## 6. Deployment shapes

### 6.1 Local dev

```bash
# Backend (in-memory + plaintext-only allowed)
.venv312/bin/python -m uvicorn app.main:app --reload --app-dir apps/api
# or:
./scripts/demo-backend.sh

# Frontend (apps/web)
cd apps/web && npm run dev      # http://localhost:5173

# Widget preview (apps/widget mounted via apps/widget-preview)
./scripts/demo-frontend.sh      # http://localhost:5174
```

### 6.2 Persistent local demo

```bash
KW_PERSISTENT=true \
KW_CORS_ALLOWED_ORIGINS=http://localhost:5173 \
KW_ALLOWED_CONTENT_TYPES=text/plain,application/pdf,...,...presentationml.presentation \
.venv312/bin/python -m uvicorn app.main:app --reload --app-dir apps/api

# or:
.venv312/bin/kw-demo
```

`.kw-pipeline/` carries `catalog.sqlite3` + raw + extractions +
semantic + markdown.

### 6.3 Knowledge-layer enabled (opt-in)

```bash
docker compose -f docker/docker-compose.yml up -d neo4j
export KW_KNOWLEDGE_LAYER_ENABLED=true
export KW_NEO4J_URI=bolt://localhost:7687
export KW_NEO4J_USER=neo4j
export KW_NEO4J_PASSWORD=test_password_change_me
export ANTHROPIC_API_KEY=sk-ant-…   # optional, for Phase 2
export VOYAGE_API_KEY=…              # optional, for Phase 3
```

### 6.4 3DEXPERIENCE deployment

Both `apps/widget` and `apps/explorer` ship as 3DDashboard widgets.
Their Webpack build produces a single bundle each; the host loads
it through `@widget-lab/3ddashboard-utils`. Today the developer
must clone `3ddashboard-utils` into `~/.kw-pipeline/3ddashboard-utils/`
to satisfy the file-link dependency. A real deployment requires:

- official Dassault Systèmes brand tokens (replaces local theme),
- 3DX session/identity context handoff (#83),
- workspace scoping predicate on every API call (#91),
- a deployment matrix (today undocumented — EPIC 10 in the backlog
  restructure doc).

---

## 7. Glossary

| Term | Meaning |
|---|---|
| KW Pipeline | the umbrella project (this repo) |
| Harvester | the backend ingestion + semantic extraction agent role; lives in `apps/api` |
| Orbital | the reviewer SPA agent role; lives in `apps/web` |
| KnowledgeForge widget | the 3DX **operate** tile; lives in `apps/widget` |
| Knowledge Explorer | the 3DX **navigate** tile; lives in `apps/explorer` |
| Document family | a `Document` that owns one or more `DocumentVersion`s of the same logical asset |
| Version | a `DocumentVersion` — immutable bytes + lifecycle state |
| Section | a parser-produced unit of a version; inherits structure (heading, page, …) |
| Chunk | a knowledge-layer unit projected from a section; the addressable thing in the graph |
| Topic | a connected-component cluster of chunks (deterministic) |
| Entity | an LLM-extracted node with section-level citations (Phase 2, opt-in) |
| Validated | reviewer-accepted; the gate that authorises projection into the knowledge graph |
| Fake* | in-memory test adapter for an optional integration (`FakeLLMClient`, `FakeEmbeddingClient`, `InMemoryGraphStore`) |
| ADR | Architecture Decision Record under `docs/adr/` |

---

*Generated 2026-05-04. Source files referenced (selected):*
*`apps/api/app/{routes.py,settings.py,services/**}` ·*
*`apps/web/{package.json,src/**}` · `apps/widget/{package.json,src/**}` ·*
*`apps/explorer/{package.json,src/{App.tsx,components/**,state/**}}` ·*
*`apps/widget-preview/**` · all 13 ADRs · `docs/architecture/{knowledge_layer.md,*.md}` ·*
*`docs/roadmap/2026-05-04-backlog-restructure.md`.*
