# Markdown Output Template

Every document version produces one Markdown file after semantic extraction.

## Required Frontmatter

```yaml
---
document_id: "<document id>"
version_id: "<version id>"
filename: "<original filename>"
sha256: "<sha-256 hash>"
parser: "<parser name>"
parser_version: "<parser version>"
extraction_date: "<ISO-8601 timestamp>"
validation_status: "needs_review"
source_uri: "<raw file uri>"
schema_version: "v0.1"
---
```

## Required Sections

1. Document Profile
2. Executive Summary
3. Key Concepts
4. Entities
5. Business Rules
6. Requirements
7. Decisions
8. Risks
9. Open Questions
10. Action Items
11. Contradictions
12. Warnings
13. Source Lineage

## Acceptance Rules

- Markdown must be deterministic for the same semantic JSON input.
- Missing sections should be rendered as empty or `None identified`, not hidden.
- Warnings must remain visible near the end of the document.
- Source lineage must include section/page/line metadata when available.
