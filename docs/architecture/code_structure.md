# Backend Code Structure

The Harvester API is split into small modules so the MVP can move from
in-memory behavior to persistent infrastructure without changing route
contracts.

## Application Wiring

- `app/main.py` creates the FastAPI app.
- `app/dependencies.py` builds one isolated `PipelineServices` container.
- `app/routes.py` registers HTTP routes against a concrete service container.

Tests use `create_app()` to get a fresh in-memory catalog and extraction store
for each integration scenario.

## Services

- `DocumentService` owns catalog behavior, SHA-256 duplicate detection, and
  lifecycle status updates.
- `PlainTextParser` is a deterministic parser used until Docling is integrated.
  It preserves original line numbers for source lineage.
- `ExtractionJobService` coordinates parser execution and status transitions.
- `SemanticExtractor` converts raw extraction output into schema-validated
  semantic JSON that remains `needs_review`.
- `MarkdownGenerator` renders deterministic Markdown with required frontmatter
  and source lineage.

## Schemas

Pydantic schemas define the current API and storage contracts:

- `Document` and `DocumentVersion`
- `RawExtraction` and `SourceReference`
- `SemanticDocument`, `SemanticSection`, and `SemanticAsset`

The schema layer enforces important quality rules, including that
`source_backed` semantic assets must include source references.

