# Customer Demo Smoke

Issue #93 owns a repeatable local path that exercises the Document
Intelligence MVP without requiring a live server. The smoke runner drives the
FastAPI routes with `TestClient`, so it covers upload guardrails, SHA-256
cataloging, parser dispatch, raw extraction, semantic JSON generation,
Markdown preview, and review validation.

## Dataset

The demo fixtures live in `apps/api/fixtures/customer_demo/`:

- `acme_supplier_onboarding_policy_v1.txt`
- `acme_supplier_onboarding_policy_v2.txt`
- `customer_success_brief.txt`
- `acme_contract_review_memo.json`, which is rendered into a DOCX during the run

The script uploads the first supplier policy, appends the second policy as
version 2 of the same document family, uploads the customer success brief as a
separate text document, uploads the generated DOCX contract memo, and uploads a
duplicate copy of the first policy to verify duplicate detection.

## Run It

Install the API with test dependencies from the repo root:

```bash
python3.12 -m venv .venv312
.venv312/bin/python -m pip install -e 'apps/api[test]'
```

Run the smoke demo:

```bash
.venv312/bin/python apps/api/scripts/customer_demo_smoke.py --reset
```

`--reset` deletes only the selected generated demo directories. By default
those are under `.kw-pipeline/customer-demo/`, which is ignored by Git.

## Outputs

The default run writes:

- `.kw-pipeline/customer-demo/data/catalog.sqlite3`
- `.kw-pipeline/customer-demo/data/raw/`
- `.kw-pipeline/customer-demo/artifacts/catalog.json`
- `.kw-pipeline/customer-demo/artifacts/run_summary.json`
- `.kw-pipeline/customer-demo/artifacts/extraction/*.json`
- `.kw-pipeline/customer-demo/artifacts/semantic/*.json`
- `.kw-pipeline/customer-demo/artifacts/markdown/*.md`

Use `run_summary.json` as the quick demo receipt. It records each uploaded
version, parser name, SHA-256 digest, source reference count, Markdown artifact,
and final `VALIDATED` status.

## Test Coverage

The smoke runner has focused pytest coverage:

```bash
.venv312/bin/python -m pytest apps/api/tests/test_customer_demo_smoke.py
```

The test asserts that representative text and DOCX documents reach
`needs_review`, are validated, produce Markdown with source lineage, write
artifacts, preserve a two-version document family, and refuse extraction for
the duplicate upload.
