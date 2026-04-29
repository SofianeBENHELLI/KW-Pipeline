# ADR-003: Semantic Markdown Output

## Status

Accepted

## Context

Semantic extraction must produce an artifact that humans can inspect, diff, and
approve.

## Decision

Generate one Markdown file per document version from schema-validated semantic
JSON. Include YAML frontmatter with document, version, hash, parser, extraction
date, validation status, and source URI.

## Consequences

- Semantic assets are portable and reviewable.
- Markdown can be displayed by Orbital without custom binary viewers.
- Missing warnings or lineage become visible quality failures.
