# Demo seed corpus

Small, reviewable, fully synthetic fixtures used by
`apps/api/scripts/seed_demo.py` to populate a local demo backend.
Nothing in this directory is customer data.

| File | Purpose | Expected outcome when seeded |
|---|---|---|
| `supplier_quality_policy_v1.txt` | A clean text policy doc (~30 lines). | Uploaded as a brand-new document family, then `extract` + `semantic` run, leaving status `NEEDS_REVIEW`. |
| `supplier_quality_policy_v2.txt` | A revised version of v1 (different text). | Uploaded **as a new version of v1's document family** (`document_id=...`), so the catalog shows version_number 2 alongside v1's version 1. Lifecycle stops at `STORED` by default. |
| `supplier_quality_policy_v1_renamed.txt` | **Byte-identical to v1**, just renamed. | Uploaded as a fresh family; the upload route detects the duplicate via SHA-256 and the response status is `DUPLICATE_DETECTED` with `duplicate_of_version_id` pointing at v1's version_id. This is the duplicate-detection demo. |
| `change_request.pdf` | A small one-page PDF (~3-5 KB). | Uploaded with content-type `application/pdf`, `PdfParser` runs on `extract`, semantic is generated. Materialised on first run by `apps/api/scripts/_demo_fixtures.py` rather than committed (PDFs embed creation timestamps and produce noisy diffs). |
| `meeting_notes.docx` | A tiny DOCX with three paragraphs. | Uploaded with the DOCX content-type, `DocxParser` runs, semantic is generated. Same materialise-on-first-run treatment as the PDF. |
| `empty.txt` | Zero bytes. | Upload returns HTTP 400 ("Uploaded file is empty.") — drives the negative-path demo. The seed script logs the failure and continues. |

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
