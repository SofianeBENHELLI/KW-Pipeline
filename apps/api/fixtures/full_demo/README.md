# Full demo dataset

A richer, fully synthetic corpus used by `apps/api/scripts/load_demo_dataset.py`
(console script: `kw-demo-load`) to populate a local KW-Pipeline backend with
enough content to exercise every user-visible feature in one pass:

- **Documents and chunks** — every fixture is uploaded, parsed, and a semantic
  document is generated.
- **Versions and lineage** — `supplier_onboarding_policy_v1/v2/v3.txt` are
  uploaded into the same document family and validated in order so the lineage
  view shows v1/v2 as `SUPERSEDED` and v3 as the current `VALIDATED` head.
- **Duplicate detection** — the v1 bytes are re-uploaded under a different
  filename and surface as `DUPLICATE_DETECTED` with the original
  `duplicate_of_version_id` populated.
- **Topic clustering** — fixtures are deliberately grouped into four topical
  clusters so the projector emits visible `topic` nodes and chunk-to-chunk
  semantic relations:
  - Quality & Compliance (ISO 9001 handbook + audit findings + CAPA log)
  - Supplier Management (onboarding policy v1/v2/v3 + qualification checklist)
  - Customer Success (renewal brief + playbook)
  - Engineering Change (change request + design review minutes)
- **Hybrid taxonomy** — `taxonomy.yaml` ships an operator-imposed taxonomy that
  overlaps with the auto-deduced clusters. The loader script wires
  `KW_TAXONOMY_PATH` so `GET /knowledge/taxonomy` returns the merged
  imposed + computed view (imposed wins on conflict, per ADR-017).
- **Knowledge graph** — every validated version is projected; the resulting
  graph carries `Document → Version → Section → Chunk` plus `topic` nodes
  and `same_topic_as` / `shares_keyword` edges that the
  `<KnowledgeGraphView />` panel renders.
- **Similarity / linking** — because multiple documents share the topic ids,
  `GET /documents/{id}/similar` returns ranked neighbours by topic-Jaccard.
- **Review lifecycle** — the loader drives every fixture through
  `extract → semantic → validate`. One fixture (`engineering_design_review_minutes.txt`)
  is intentionally `reject`-ed to demonstrate the rejection path.

All content is synthetic; nothing here is customer data.
