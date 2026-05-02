# Demo presenter-path smoke test

`apps/api/tests/test_demo_smoke.py` exercises the end-to-end loop a
presenter walks during a live demo: upload → catalog → extract →
semantic → review. It exists so that breakage in any single step of the
demo path surfaces immediately, with a failure message that names the
broken step rather than just dumping a stack trace.

## What it covers

Two tests, no markers, default `pytest` invocation.

- **`test_presenter_demo_path`** — the happy path. Uploads a tiny
  deterministic plain-text fixture, asserts the catalog lists it, runs
  `extract`, retrieves the raw extraction, runs `semantic`, fetches the
  generated markdown, sanity-checks the version is in `NEEDS_REVIEW`,
  then validates with a reviewer note. Final assertion confirms status
  `VALIDATED` and that the reviewer note round-tripped to the catalog.

- **`test_presenter_reject_path`** — the rejection variant. Same upload
  → extract → semantic prefix, then `reject` and assert status
  `REJECTED` plus reviewer-note persistence.

Every assertion carries a step label (`"Step 3 (extract) failed: ..."`)
so a failure points unambiguously at the demo step that broke.

## What it deliberately does not cover

- **Network**. The test uses FastAPI's in-process `TestClient`. A
  network-level smoke check is the seed script (`apps/api/scripts/seed_demo.py`),
  which is the operator's tool. The two are independent on purpose:
  the smoke test must keep running in CI even when no API is up.
- **Persistent mode**. The test runs against the default in-memory
  backend. Persistent-mode coverage lives in
  `tests/test_persistent_catalog.py`.
- **PDF / DOCX parsers**. Smoke tests stay tiny and deterministic;
  parser coverage is in `tests/test_pdf_parser.py` and
  `tests/test_docx_parser.py`. The seed script exercises these
  end-to-end against a live backend.

## Running it standalone

From the repo root:

```bash
.venv312/bin/python -m pytest apps/api/tests/test_demo_smoke.py -v
```

Or, with the venv activated and CWD `apps/api/`:

```bash
python -m pytest tests/test_demo_smoke.py -v
```

Both tests should pass in well under a second on a developer laptop —
if either takes longer than a few seconds, treat that as a regression
in startup or fixture loading.

## Relationship to the seed script

The smoke test and `apps/api/scripts/seed_demo.py` cover the same
pipeline path but at different layers:

| | Smoke test | Seed script |
|---|---|---|
| Transport | In-process `TestClient` | `httpx` over HTTP |
| Backend | In-memory only | In-memory or persistent |
| Audience | CI + every developer | Demo operator |
| Failure mode | Pytest assertion | Summary table row |

They are intentionally independent: a broken seed script must not break
CI, and a broken smoke test must not block a presenter from seeding a
live backend by hand.
