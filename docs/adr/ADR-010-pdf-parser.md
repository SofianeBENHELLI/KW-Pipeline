# ADR-010: PDF parser library — pdfplumber for the MVP

Status: accepted, 2026-05-01.

## Context

Issue #45 calls for a PDF parser conforming to the `Parser` Protocol so the
catalog can ingest the bulk of likely real input. The issue title proposes
**Docling** as the implementation. Before adopting Docling we evaluated it
against the MVP's actual demo and CI constraints.

## Decision

Use **pdfplumber** (>= 0.11) as the PDF parser library for the MVP.

`PdfParser` lives at `apps/api/app/services/parsers/pdf.py` and is registered
for `application/pdf` in `app.dependencies._build_parser_registry`.

## Why not Docling

Docling is the higher-quality option on a per-document basis: ML-driven
layout analysis, table structure recovery, and figure semantics. The cost
profile is wrong for the MVP demo path:

- **Cold-start weight**: Docling pulls `torch` (~200 MB wheel) plus
  transformers and downloads several hundred MB of model weights on first
  invocation. That changes our `pip install -e 'apps/api[test]'` from a
  ~30 second operation into a multi-minute one and adds a network
  dependency to the first parse.
- **CI cost**: every PR's backend job would either re-download the models
  (slow + flaky) or warm a cache (extra workflow plumbing). pdfplumber adds
  one pure-Python wheel and a small native binding (`pypdfium2`).
- **Demo experience**: the first PDF a user uploads in a fresh demo env
  would block on the model download. pdfplumber returns within
  milliseconds.

The acceptance criteria called out four observable outputs:

1. `application/pdf` registered → covered.
2. Text + `SourceReference` rows with `page_number` populated → covered;
   pdfplumber is page-native.
3. Table metadata captured in `RawExtraction.sections` where available →
   covered via `Page.extract_tables()`; we emit a section per table with
   `parser_metadata={"page_number", "table_index"}`.
4. Figure metadata where available → **not covered**; pdfplumber has no
   figure understanding. Open a follow-up issue if/when a customer needs
   it.

So the MVP retains 3 of the 4 functional outputs at a fraction of the
install / runtime cost.

## License review

pdfplumber is MIT-licensed (Jeremy Singer-Vine, 2015–present). Its primary
runtime dependency `pypdfium2` is BSD-3-Clause. Both are compatible with
this project's commercial-use needs. No CLA or attribution requirements
beyond preserving the license text in source distributions.

## Consequences

- **Test deps**: `fpdf2` (MIT, pure Python) is added under
  `[project.optional-dependencies].test` so unit tests can build PDFs in
  memory without committing many fixture binaries.
- **OCR boundary**: PDFs whose pages are scanned images (no embedded text)
  will produce empty pages. The parser surfaces this as a warning that
  points at issue #47 (OCR), so the lifecycle FSM and reviewers see a
  clear signal rather than silent empty output.
- **Future Docling track**: keep #45 closed; if richer figure/layout
  semantics become a real customer ask, open a new issue
  ("Docling-quality PDF parser as a second parser") and register it for a
  more specific MIME or as an opt-in via env var. The `ParserRegistry`
  already supports first-registered-wins, so an opt-in switch is cheap.
