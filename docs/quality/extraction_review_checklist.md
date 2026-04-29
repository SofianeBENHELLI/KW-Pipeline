# Extraction Review Checklist

Use this checklist before marking generated semantic output as validated.

## Blocking

- Missing SHA-256 hash.
- Missing document version ID.
- Missing parser metadata.
- Semantic JSON failed schema validation.
- Markdown frontmatter is missing required fields.
- Unsupported claims are marked trusted.
- Source lineage is absent for critical claims.

## Major

- Low-confidence assets are not visually flagged.
- Parser only partially extracted the document.
- Tables or structured sections are missing from raw extraction.
- Contradictions are present but not flagged.

## Minor

- Title is filename-derived but acceptable.
- Some optional profile fields are unknown.
- Source line numbers are unavailable but page or section references exist.

## Accepted

- Unknown audience when source does not state audience.
- Empty action items when no action-oriented language exists.
- Empty decisions when no decision language exists.
