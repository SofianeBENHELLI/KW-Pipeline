# KW Pipeline

KW Pipeline is a document intelligence MVP focused on auditable ingestion,
deterministic parsing, governed semantic extraction, and reviewable Markdown
outputs.

The first implementation target is intentionally narrow:

- upload and catalog documents;
- compute immutable SHA-256 hashes;
- detect duplicate binary uploads;
- preserve document version lineage;
- parse raw document content into inspectable extraction JSON;
- transform raw extraction into schema-validated semantic JSON;
- generate one Markdown asset per document version;
- keep all unverified semantic claims in `needs_review`.

See `docs/architecture/document_intelligence_mvp.md` for the initial contract.

## Development

Create a Python 3.12 virtual environment and install the API package with test
dependencies:

```bash
python3.12 -m venv .venv312
.venv312/bin/python -m pip install -e 'apps/api[test]'
```

Run the backend test suite:

```bash
.venv312/bin/python -m pytest apps/api/tests
```

## Local Persistence

The API can run with in-memory services for tests or local persistent services
for MVP demos. Persistent mode stores SQLite catalog metadata and raw files
under `.kw-pipeline/`, which is ignored by Git.

```python
from app.main import create_app

app = create_app(persistent=True)
```

Persistent mode creates:

- `.kw-pipeline/catalog.sqlite3`
- `.kw-pipeline/raw/`

Delete `.kw-pipeline/` to reset local MVP state.
