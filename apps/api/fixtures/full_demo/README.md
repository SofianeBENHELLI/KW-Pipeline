# Full demo dataset — automotive OEM corpus

A rich, fully synthetic corpus used by `apps/api/scripts/load_demo_dataset.py`
(console script: `kw-demo-load`) to populate a local KW-Pipeline backend with
enough content to feel like a real automotive-OEM document set and to exercise
every user-visible feature in one pass.

## Functions covered

The corpus is themed around an EV program (NX-EV-2027 sedan) and spans the
full automotive lifecycle:

| Cluster | Fixtures | Sample contents |
|---|---|---|
| Engineering | 5 | HV battery architecture, ADAS L2+ feature definition, vehicle dynamics targets, E/E architecture, top-level BOM |
| Manufacturing | 5 | Body-shop welding cell, paint-shop process, final-assembly takt, MES/SCADA integration, line-balancing study |
| Quality & Compliance | 3 | ISO 9001 handbook, Q1 audit findings, CAPA log |
| Validation & Verification | 4 | ADAS HIL strategy, Euro NCAP crash plan, durability validation, ISO 26262 ASIL D log |
| Sourcing & Supply Chain | 3 | Tier-1 scorecard, dual-sourcing IGBT/SiC, RFQ inverter |
| Marketing | 3 | Urban-EV segment study, sedan-vs-SUV positioning, launch press kit |
| Simulation / CAE | 3 | CFD aero, frontal MPDB crash, NVH powertrain |
| Cybersecurity | 2 | UNECE R155 policy, TARA HMI |
| Homologation | 2 | Euro 7 emissions plan, EU type approval roadmap |
| Suppliers | 1 | Supplier qualification checklist |
| Customer Success | 2 | Renewal brief, success playbook |
| Engineering Change | 2 | Change request CR-4471, design review minutes |
| **Multi-version family — Supplier onboarding policy** | 3 (v1 → v2 → v3) | Demonstrates auto-supersede on the review FSM |
| **Multi-version family — ECU software architecture** | 3 (v1 → v2 → v3) | Second supersede demo, themed around AUTOSAR Adaptive |

That is **41 single-version fixtures + 2 three-version families = 47 versions**
across **13 clusters**, plus PDF and DOCX cousins materialised on first run for
the binary parsers, plus one rename of v1 to fire `DUPLICATE_DETECTED`.

## Features exercised

- **Documents and chunks** — every fixture is uploaded, parsed, and a semantic
  document is generated.
- **Versions and lineage** — both multi-version families (`supplier_onboarding_policy_v1/v2/v3`
  and `ecu_software_architecture_v1/v2/v3`) are uploaded into a single document
  each and validated in order, so the lineage view shows v1/v2 as `SUPERSEDED`
  and v3 as the current `VALIDATED` head for both families.
- **Duplicate detection** — the supplier-onboarding v1 bytes are re-uploaded
  under a renamed filename and surface as `DUPLICATE_DETECTED` with the
  original `duplicate_of_version_id` populated.
- **Topic clustering** — fixtures are written so each cluster shares strong
  vocabulary (e.g. every V&V doc mentions HIL/SIL/ASIL/test pyramid; every
  manufacturing doc mentions takt/MES/Plant 2). The projector emits visible
  `topic` nodes and chunk-to-chunk `same_topic_as` / `shares_keyword` edges.
- **Hybrid taxonomy** — `taxonomy.yaml` ships an operator-imposed taxonomy
  with 13 top-level categories (and ~25 subcategories) that overlap with the
  auto-deduced clusters. The loader script wires `KW_TAXONOMY_PATH` so
  `GET /knowledge/taxonomy` returns the merged imposed + computed view
  (imposed wins on conflict, per ADR-017).
- **Knowledge graph** — every validated version is projected; the graph
  carries `Document → Version → Section → Chunk` plus `topic` nodes and
  `same_topic_as` / `shares_keyword` edges that the `<KnowledgeGraphView />`
  panel renders.
- **Similarity / linking** — fixtures cite each other on purpose
  (e.g. the FMEA-style ECU SW v3 references the cybersecurity TARA HMI
  document; the type-approval roadmap references the Euro 7 plan; the
  marketing launch plan references the Euro NCAP crash test plan), so
  `GET /documents/{id}/similar` returns ranked neighbours by topic-Jaccard.
- **Review lifecycle** — the loader drives every fixture through
  `extract → semantic → validate`. One fixture
  (`engineering_design_review_minutes.txt`) is intentionally `reject`-ed to
  demonstrate the rejection path.
- **Mixed parsers** — text fixtures dominate, plus a PDF and a DOCX
  materialised on first run via the existing `_demo_fixtures.py` helpers.

## Synthetic data disclaimer

All content is fully synthetic. The "NX-EV-2027" program, the suppliers, the
plant numbers, the dummies, the regulator references, and the people are
made up. Nothing here is customer data or proprietary OEM data.
