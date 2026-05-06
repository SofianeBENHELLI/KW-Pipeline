# Convenience targets for the customer KG demo (#152).
#
# Each target is a short alias around an underlying command — keep them
# trivial so a fresh contributor can read this file and understand what
# happens. The full presenter runbook lives in
# docs/demo/customer_kg_demo.md.

PYTHON ?= python3
API_DIR := apps/api
WEB_DIR := apps/web
WIDGET_DIR := apps/widget
COMPOSE := docker compose -f docker/docker-compose.yml

.PHONY: help demo-smoke demo-api demo-web demo-widget demo-neo4j demo-graph demo-load

help:
	@echo "KW-Pipeline demo targets:"
	@echo "  make demo-smoke   Run the customer demo smoke pipeline (no browser)"
	@echo "  make demo-api     Start the API with kg-demo defaults (kw-demo)"
	@echo "  make demo-web     Start the Vite dev server for Orbital"
	@echo "  make demo-widget  Start the 3DX KnowledgeForge widget dev server (https://localhost:8081)"
	@echo "  make demo-neo4j   Bring up the optional Neo4j store via docker compose"
	@echo "  make demo-graph   Alias of demo-neo4j"
	@echo "  make demo-load    Load the full demo dataset against a running demo-api"
	@echo ""
	@echo "See docs/demo/customer_kg_demo.md for the full runbook."

# Path 1 — smoke run, no browser. Drives upload → extract → semantic →
# review → graph projection via TestClient and writes JSON artifacts to
# .kw-pipeline/customer-demo/artifacts/. No Neo4j, no Anthropic.
demo-smoke:
	cd $(API_DIR) && $(PYTHON) scripts/customer_demo_smoke.py

# Path 2 — live API. Wraps `kw-demo` (apps/api/app/demo.py) which sets
# KW_PERSISTENT, KW_KNOWLEDGE_LAYER_ENABLED, and the demo content-type
# allowlist before starting uvicorn on 127.0.0.1:8000.
demo-api:
	cd $(API_DIR) && $(PYTHON) -m app.demo

# Path 2 — live web. Vite dev server on http://localhost:5173.
# `kw-demo` already configures CORS so this just works.
demo-web:
	cd $(WEB_DIR) && npm run dev

# Path 2 — 3DEXPERIENCE widget dev server on https://localhost:8081/widget.
# Requires the @widget-lab npm registry token configured in `~/.npmrc`
# (see apps/widget/README.md). `kw-demo` includes the widget origin in
# its default CORS allowlist so this just works against `make demo-api`.
demo-widget:
	cd $(WIDGET_DIR) && npm install && npm start

# Path 3 (optional) — bring up Neo4j so KW_NEO4J_URI=bolt://localhost:7687
# can drive the projector against a real graph database.
demo-neo4j:
	$(COMPOSE) up -d neo4j

# Friendly alias.
demo-graph: demo-neo4j

# Full demo loader — populates a running demo-api with the rich corpus
# under apps/api/fixtures/full_demo/ so the UI can demonstrate
# documents, chunks, taxonomy, version lineage, duplicate detection,
# topic clustering, knowledge graph, similarity, and the review FSM.
# Requires `make demo-api` (or ./scripts/demo-backend.sh) to be running
# in another terminal first.
demo-load:
	cd $(API_DIR) && $(PYTHON) scripts/load_demo_dataset.py
