# Semantic JSON Contract

## Schema Version

The initial schema version is `v0.1`.

## Required Top-Level Fields

- `schema_version`
- `document_profile`
- `sections`
- `assets`
- `warnings`
- `source_references`
- `validation_status`

## Document Profile

| Field | Required | Notes |
| --- | --- | --- |
| `title` | Yes | Best available title or filename-derived title. |
| `document_type` | Yes | Example: policy, report, contract, manual, unknown. |
| `purpose` | No | Must be `needs_review` if inferred. |
| `audience` | No | Must be `needs_review` if inferred. |
| `executive_summary` | No | Must be source-backed or flagged. |

## Semantic Assets

Each semantic asset must include:

- `id`
- `type`
- `text`
- `confidence`
- `review_status`
- `source_reference_ids`

Supported initial asset types:

- `key_concept`
- `entity`
- `glossary_term`
- `taxonomy_tag`
- `business_rule`
- `requirement`
- `decision`
- `risk`
- `open_question`
- `action_item`
- `contradiction`

## Review Status

| Status | Meaning |
| --- | --- |
| `needs_review` | Default for inferred or weakly supported content. |
| `source_backed` | Directly supported by source references. |
| `validated` | Human reviewer accepted the asset. |
| `rejected` | Human reviewer rejected the asset. |

## Warnings

Warnings must be emitted for:

- missing source lineage;
- low confidence assets;
- parser failures or partial extraction;
- unsupported claims;
- contradictory content;
- schema coercion or dropped fields.
