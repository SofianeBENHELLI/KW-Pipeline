# ADR-001: Document Intelligence MVP

## Status

Accepted

## Context

The MVP needs to prove that uploaded documents can become reviewable semantic
Markdown assets without losing auditability. The main risk is producing
impressive but untrusted semantic output.

## Decision

Build a pipeline around immutable document versions, SHA-256 hashing,
inspectable raw extraction JSON, schema-validated semantic JSON, and generated
Markdown marked `NEEDS_REVIEW` by default.

## Consequences

- Backend and frontend can build in parallel from explicit contracts.
- Human review is required before semantic output is trusted.
- Parser failures and missing lineage remain visible.
- Later LLM extraction can be added only behind schema validation.
