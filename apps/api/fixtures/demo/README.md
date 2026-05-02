# Demo seed corpus

Small, reviewable, fully synthetic fixtures used by
`apps/api/scripts/seed_demo.py` to populate a local demo backend.
Nothing in this directory is customer data.

| File | Purpose | Expected outcome when seeded |
|---|---|---|
| `supplier_quality_policy_v1.txt` | A clean text policy doc (~40 lines, seven numbered sections). | Uploaded as a brand-new document family, then `extract` + `semantic` run, leaving status `NEEDS_REVIEW`. |
| `supplier_quality_policy_v2.txt` | A revised version of v1 — same section structure, tightened AQL / containment / retention numbers, added contract-manufacturer scope and supplier-quality-engineer review. | Uploaded **as a new version of v1's document family** (`document_id=...`), so the catalog shows version_number 2 alongside v1's version 1. Lifecycle stops at `STORED` by default. |
| `supplier_quality_policy_v1_renamed.txt` | **Byte-identical to v1**, just renamed. | Uploaded as a fresh family; the upload route detects the duplicate via SHA-256 and the response status is `DUPLICATE_DETECTED` with `duplicate_of_version_id` pointing at v1's version_id. This is the duplicate-detection demo. |
| `change_request.pdf` | Engineering change request CR-2026-0142 covering a fastener-bracket part-number swap on Line 3. Materialised on first run by `apps/api/scripts/_demo_fixtures.py` rather than committed. | Uploaded with content-type `application/pdf`, `PdfParser` runs on `extract`, semantic is generated. |
| `meeting_notes.docx` | Weekly quality review meeting notes (six paragraphs covering NCR status, AQL trend, the engineering change request, and action items). Same materialise-on-first-run treatment as the PDF. | Uploaded with the DOCX content-type, `DocxParser` runs, semantic is generated. |
| `empty.txt` | Zero bytes. | Upload returns HTTP 400 ("Uploaded file is empty.") — drives the negative-path demo. The seed script logs the failure and continues. |

## Demos preserved by these fixtures

- **Duplicate detection.** `supplier_quality_policy_v1_renamed.txt` is
  bit-for-bit identical to `supplier_quality_policy_v1.txt`. The
  upload route fingerprints both with the same SHA-256, returns
  `DUPLICATE_DETECTED`, and points the second upload's
  `duplicate_of_version_id` at v1's version_id.
- **Version lineage.** `supplier_quality_policy_v2.txt` is uploaded
  with `document_id=<v1.document_id>` so the catalog shows a single
  document family with two versions, not two parallel families.
- **Negative path.** `empty.txt` exercises the upload byte-floor
  guardrail.
- **PDF and DOCX parsers.** `change_request.pdf` and
  `meeting_notes.docx` route through `PdfParser` and `DocxParser`
  respectively so the demo covers all three parser implementations.

## Visual graph clarity (issue #147)

The fixtures are tuned so the v0.2 knowledge-graph projection (once
issue #144 wires chunks/topics/relations into the projector) renders
as a recognizably-clustered graph rather than a single uniform blob.
Each fixture commits to a distinct vocabulary so a deterministic
keyword-overlap relation extractor can place chunks into one of three
clusters:

- **Cluster A — Supplier Quality Policy.** Driven by
  `supplier_quality_policy_v1.txt`, `supplier_quality_policy_v2.txt`,
  and `supplier_quality_policy_v1_renamed.txt`. Anchor keywords:
  *supplier*, *AQL*, *inspection*, *non-conformance report*,
  *audit*, *quality management system*, *vendor list*. v1 and v2
  share section headings (Purpose / Scope / Inspection /
  Non-Conformance / Audits / Records / Revisions) so the projector
  has obvious intra-cluster `same_topic_as` relations between
  matching sections across the two versions.
- **Cluster B — Engineering Change Request.** Driven by
  `change_request.pdf`. Anchor keywords: *engineering change*,
  *fastener bracket*, *part number 4471-A / 4471-B*, *Line 3*,
  *work-in-process*, *engineering change board*. Distinct from
  Cluster A's quality-system vocabulary, so a keyword-overlap
  extractor places its chunks into a separate topic.
- **Cluster C — Quality Review Meeting.** Driven by
  `meeting_notes.docx`. Anchor keywords: *weekly quality review
  meeting*, *attendees*, *agenda*, *action item*. The meeting
  notes are intentionally written as a discussion record rather
  than a policy or change order, so the dominant vocabulary
  ("agenda", "action items", "attendees") is distinct from both
  policy and change-request language.

**Inter-cluster relations.** The DOCX in Cluster C name-checks the
Supplier Quality Policy (Cluster A) — it cites *non-conformance
report*, *containment window*, *AQL 1.5* — and the engineering
change request (Cluster B) — *CR-2026-0142*, *fastener bracket*,
*part number 4471-A / 4471-B*, *Line 3*. The change request in
Cluster B in turn name-checks the Supplier Quality Policy
(*receiving inspection AQL*, *Supplier Quality Policy*). Those
shared tokens give a deterministic keyword-overlap extractor at
least two `related_to` edges between distinct clusters, so the
final graph is not a set of disconnected islands.

The actual richness of the projected graph depends on Wave 2's
issue #144 landing the relation/clustering services; the fixture
content is tuned so that work has obvious signal to find. See
`docs/architecture/knowledge_layer.md` for the v0.x payload shape.

## Why some fixtures are committed and others generated

Plain-text fixtures are committed verbatim — they diff cleanly and the
duplicate-detection demo only works if v1 and v1_renamed are bit-for-bit
identical, which is trivial to maintain in tracked files.

The PDF and DOCX, by contrast, embed creation metadata (timestamps,
non-deterministic IDs). Committing them would produce churn on every
regeneration and add ~40 KB of binary noise to the repo. Instead the
seed script materialises them on first run via
`apps/api/scripts/_demo_fixtures.py`, which uses `fpdf2` and
`python-docx` (already test-extra dependencies). Re-running the seed
script is a no-op for these files once they exist.
