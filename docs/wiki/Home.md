# KW Pipeline Wiki

KW Pipeline is an **auditable document-intelligence pipeline**: documents go in, schema-validated semantic Markdown comes out, a human reviewer validates or rejects each version, and an **opt-in knowledge graph + LLM entity extractor** layers on top — strictly behind the review gate.

This wiki is the entry point for newcomers. Prefer the in-repo docs for anything that has to stay version-locked with the code:

- [`README.md`](https://github.com/SofianeBENHELLI/KW-Pipeline/blob/main/README.md) — setup + opt-in env vars.
- [`AGENTS.md`](https://github.com/SofianeBENHELLI/KW-Pipeline/blob/main/AGENTS.md) — engineering rules + agent roles.
- [`docs/architecture/`](https://github.com/SofianeBENHELLI/KW-Pipeline/tree/main/docs/architecture) — architecture (canonical).
- [`docs/adr/`](https://github.com/SofianeBENHELLI/KW-Pipeline/tree/main/docs/adr) — Architecture Decision Records.
- [`docs/roadmap/mvp_backlog_review.md`](https://github.com/SofianeBENHELLI/KW-Pipeline/blob/main/docs/roadmap/mvp_backlog_review.md) — current backlog + work order.

## Wiki pages

- **[Overview](Overview)** — what the system does, in three minutes.
- **[Architecture](Architecture)** — the core pipeline and the optional knowledge layer side-by-side.
- **[Knowledge Layer](Knowledge-Layer)** — graph projection, LLM entity extraction, audit guarantees.
- **[Operating Modes](Operating-Modes)** — env vars, `docker compose up`, integration / LLM tests.
- **[Decisions](Decisions)** — fast index of every ADR with one-line summaries.
- **[Roadmap](Roadmap)** — what shipped, what's next, what's deferred.

## Two-line summary

KW Pipeline turns a document upload into:

1. an immutable hash + version + raw extraction + schema-validated semantic JSON + Markdown asset, every claim with source-line lineage;
2. (opt-in) a Neo4j subgraph of `Document → Version → Section → Entity` populated *after* a human validates the version, where every edge carries a `source_reference_id` — no edge in the graph without provenance.

## License

The repo is private at the moment; treat the wiki as project-internal documentation. Vendored patterns from
[`neo4j-labs/llm-graph-builder`](https://github.com/neo4j-labs/llm-graph-builder) are Apache-2.0 and credited at the call sites.
