"""Guard the committed OpenAPI snapshot against backend drift.

The frontend's typed API client (issue #80) is generated from
``apps/api/openapi.json``. This test fails any backend PR that changes the
HTTP contract without regenerating the snapshot, ensuring the committed
file is always a faithful render of the live FastAPI app.

When this test fails, run ``python scripts/export_openapi.py`` from
``apps/api/`` and commit the updated ``openapi.json`` (plus the
regenerated frontend types — see ``docs/workflows/openapi_codegen.md``).
"""

from pathlib import Path

from scripts.export_openapi import render_openapi


def test_openapi_snapshot_matches_committed_file():
    snapshot = Path(__file__).resolve().parent.parent / "openapi.json"
    expected = snapshot.read_text(encoding="utf-8")
    actual = render_openapi()
    assert actual == expected, (
        "OpenAPI snapshot is stale. Run "
        "`python scripts/export_openapi.py` from apps/api/, commit the "
        "updated openapi.json, and regenerate the frontend types."
    )
