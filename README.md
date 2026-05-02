# KW Pipeline

KW Pipeline is a document intelligence MVP focused on auditable ingestion,
deterministic parsing, governed semantic extraction, reviewable Markdown
outputs, and an opt-in **knowledge graph + LLM-powered entity layer** that
sits *behind* the human review gate.

The first implementation target is intentionally narrow:

- upload and catalog documents;
- compute immutable SHA-256 hashes;
- detect duplicate binary uploads;
- preserve document version lineage;
- parse raw document content into inspectable extraction JSON;
- transform raw extraction into schema-validated semantic JSON;
- generate one Markdown asset per document version;
- keep all unverified semantic claims in `needs_review`.

After a version is `VALIDATED` by a reviewer, the optional **knowledge
layer** (ADR-012, ADR-013) projects it into a graph of
`Document → Version → Section` nodes (Phase 1) and — when an
Anthropic API key is configured — extracts typed `(:Entity)` nodes with
section-level citations (Phase 2). Every graph edge carries a
`source_reference_id`; nothing without provenance ever lands in the graph.

See [`docs/architecture/document_intelligence_mvp.md`](docs/architecture/document_intelligence_mvp.md)
for the core ingestion contract,
[`docs/architecture/knowledge_layer.md`](docs/architecture/knowledge_layer.md)
for the graph + chat surface,
and [`docs/roadmap/mvp_backlog_review.md`](docs/roadmap/mvp_backlog_review.md)
for the current backlog and remaining-work plan.

## Development

Create a Python 3.12 virtual environment and install the API package with test
dependencies:

```bash
python3.12 -m venv .venv312
.venv312/bin/python -m pip install -e 'apps/api[test]'
```

Run the backend test suite:

```bash
.venv312/bin/python -m pytest apps/api/tests
```

Install and run the frontend checks:

```bash
cd apps/web
npm ci
npm test
npm run build
```

## Local demo

For a one-paste presenter walkthrough that survives API restarts and accepts
the demo dataset (text, PDF, DOCX) out of the box, set the demo env vars and
run uvicorn against the module-level app:

```bash
KW_PERSISTENT=true \
KW_CORS_ALLOWED_ORIGINS=http://localhost:5173 \
KW_ALLOWED_CONTENT_TYPES=text/plain,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document \
.venv312/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --app-dir apps/api
```

Or, after `pip install -e 'apps/api[test]'`, the bundled console script wraps
the same defaults:

```bash
.venv312/bin/kw-demo
```

`KW_PERSISTENT=true` flips the module-level `app` to the SQLite + filesystem
services; persistent state lives under `.kw-pipeline/` (see below). Delete
that directory to reset demo state. The Vite dev server in `apps/web` reaches
the API at `http://localhost:8000` and is allowlisted by the CORS env var.

## Local Persistence

The API can run with in-memory services for tests or local persistent services
for MVP demos. Persistent mode stores SQLite catalog metadata and raw files
under `.kw-pipeline/`, which is ignored by Git.

```python
from app.main import create_app

app = create_app(persistent=True)
```

Persistent mode creates:

- `.kw-pipeline/catalog.sqlite3`
- `.kw-pipeline/raw/`

Delete `.kw-pipeline/` to reset local MVP state.

## Demo seed data

After starting the demo backend, seed deterministic demo content:

```bash
cd apps/api
# Broaden the upload allowlist so PDF/DOCX fixtures are accepted; the
# default backend only allows text/plain.
KW_ALLOWED_CONTENT_TYPES="text/plain,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" \
  uvicorn app.main:app --reload &
python scripts/seed_demo.py
```

The script uploads a small, reviewable corpus that demonstrates duplicate
detection, version lineage, and the upload → extract → semantic → review
loop. See `apps/api/fixtures/demo/README.md` for what each file shows.
Re-running the seed against an already-populated backend is harmless:
duplicate uploads simply return `DUPLICATE_DETECTED`. Pass
`--validate-one` to also flip one document to `VALIDATED` so the
optional knowledge-graph projection has something to render.

## Knowledge Layer (Optional)

The knowledge layer is **opt-in** and disabled by default. With no env vars
set, the existing pipeline behaves exactly as it did before — every existing
test still passes, no Neo4j, no LLM calls. To enable it locally:

```bash
docker compose -f docker/docker-compose.yml up -d neo4j
export KW_KNOWLEDGE_LAYER_ENABLED=true
export KW_NEO4J_URI=bolt://localhost:7687
export KW_NEO4J_USER=neo4j
export KW_NEO4J_PASSWORD=test_password_change_me
# Phase 2 (entity extraction) — also requires:
export ANTHROPIC_API_KEY=sk-ant-...
```

Validating a document then projects it into the graph as a fire-and-log
side-effect; the projection is reachable via
`GET /documents/{document_id}/graph` and
`GET /knowledge/graph` (cursor-paginated). Orbital's review workspace
includes a `<KnowledgeGraphView />` panel that renders the projection
through `@neo4j-nvl/react`.

| Env var | Purpose | Default |
|---|---|---|
| `KW_KNOWLEDGE_LAYER_ENABLED` | Master kill-switch (must be `true` to enable anything below) | unset → disabled |
| `KW_NEO4J_URI` | `bolt://...` connection string for the graph store | unset → in-memory store |
| `KW_NEO4J_USER` / `KW_NEO4J_PASSWORD` / `KW_NEO4J_DATABASE` | Auth + DB name | unset / unset / `neo4j` |
| `ANTHROPIC_API_KEY` | Required for Phase 2 entity extraction | unset → Phase 2 disabled |
| `KW_LLM_MODEL` | Claude model id | `claude-sonnet-4-5` |

The knowledge-layer surface is documented end-to-end in
[`docs/architecture/knowledge_layer.md`](docs/architecture/knowledge_layer.md).
Architecture decisions:

- [ADR-012 — Knowledge graph layer behind the review gate](docs/adr/ADR-012-knowledge-graph-layer.md)
- [ADR-013 — LLM provider (Anthropic, no LangChain)](docs/adr/ADR-013-llm-provider-and-no-langchain.md)
- [ADR-014 — Entity extraction prompt and cost guardrails](docs/adr/ADR-014-entity-extraction-prompt-and-cost.md)
