"""One-command seeder for the local demo backend.

Uploads a small, deterministic corpus that exercises the upload →
extract → semantic → review loop, plus the duplicate-detection path. The
goal is presenter-friendly: after running this script against a fresh
backend, the Orbital UI has something to render and the failure modes
worth showing (duplicate, empty file) have been triggered.

Usage
-----

    cd apps/api
    python scripts/seed_demo.py [--api http://127.0.0.1:8000]
                                [--validate-one]
                                [--reset]

The script treats the API as a black box — every interaction goes
through ``httpx`` against ``/documents/upload``, ``/extract``, and
``/semantic``. That keeps it usable against the in-memory backend, the
SQLite-persistent backend (``KW_PERSISTENT=true``), and any future
remote demo deployment without code changes.

Idempotency
-----------

Re-running the script when the same bytes already exist produces
``DUPLICATE_DETECTED`` versions on the second pass, which is harmless
and informative — it simply demonstrates duplicate detection a second
time. To truly reset state, stop the API, ``rm -rf .kw-pipeline/``, and
restart it; the ``--reset`` flag prints that recipe but does not run it
itself, since the script can't reliably know where the operator put the
data dir.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import httpx

# Make the demo-fixture helper importable regardless of CWD. The script
# is expected to be invoked from ``apps/api/`` per the README, but tests
# that import this module from elsewhere should still work.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from _demo_fixtures import (  # noqa: E402  (intentional sys.path tweak above)
    DEMO_DIR,
    DOCX_NAME,
    PDF_NAME,
    materialise_all,
)

DEFAULT_API = "http://127.0.0.1:8000"

# Content-type lookup for fixtures. We do not infer from the suffix
# inside the script because the upload route's allowlist is exact-match
# and we want the same request the UI would send.
_CONTENT_TYPES = {
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


@dataclass
class SeededVersion:
    """Result of a single fixture upload, used for the summary table."""

    fixture: str
    document_id: str | None
    version_id: str | None
    status: str
    detail: str = ""


def _content_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix not in _CONTENT_TYPES:
        raise ValueError(f"Unsupported demo fixture suffix: {path.name}")
    return _CONTENT_TYPES[suffix]


def _check_health(client: httpx.Client) -> None:
    """Ping ``GET /health``; abort with a clear error if the API is down."""
    try:
        response = client.get("/health", timeout=5.0)
    except httpx.HTTPError as exc:
        raise SystemExit(
            f"Cannot reach the demo backend at {client.base_url}.\n"
            "Start it first, e.g.:\n"
            "    cd apps/api\n"
            "    KW_PERSISTENT=true uvicorn app.main:app --reload\n"
            f"Original error: {exc}"
        ) from exc
    if response.status_code != 200:
        raise SystemExit(f"Health check returned HTTP {response.status_code}: {response.text}")


def _upload(
    client: httpx.Client,
    path: Path,
    *,
    document_id: str | None = None,
) -> SeededVersion:
    """Upload one fixture and return a row for the summary table."""
    content_type = _content_type_for(path)
    files = {"file": (path.name, path.read_bytes(), content_type)}
    params = {"document_id": document_id} if document_id else None
    response = client.post("/documents/upload", files=files, params=params)
    if response.status_code != 200:
        return SeededVersion(
            fixture=path.name,
            document_id=None,
            version_id=None,
            status=f"HTTP {response.status_code}",
            detail=response.text.strip()[:140],
        )
    body = response.json()
    return SeededVersion(
        fixture=path.name,
        document_id=body["document_id"],
        version_id=body["id"],
        status=body["status"],
    )


def _drive_to_needs_review(client: httpx.Client, version: SeededVersion) -> None:
    """Run extract + semantic so the version lands in ``NEEDS_REVIEW``.

    Failures are recorded on ``version.detail`` rather than raised so a
    seed run keeps going past one parser hiccup; the summary table at
    the end will show what actually landed where.
    """
    if version.document_id is None or version.version_id is None:
        return
    base = f"/documents/{version.document_id}/versions/{version.version_id}"
    extract = client.post(f"{base}/extract")
    if extract.status_code != 200:
        version.detail = f"extract failed: HTTP {extract.status_code}"
        return
    semantic = client.post(f"{base}/semantic")
    if semantic.status_code != 200:
        version.detail = f"semantic failed: HTTP {semantic.status_code}"
        return
    version.status = "NEEDS_REVIEW"


def _validate(client: httpx.Client, version: SeededVersion) -> None:
    """POST /validate on a single version (used by --validate-one)."""
    if version.document_id is None or version.version_id is None:
        return
    response = client.post(
        f"/documents/{version.document_id}/versions/{version.version_id}/validate",
        json={"reviewer_note": "demo seed: validated for graph projection"},
    )
    if response.status_code == 200:
        version.status = "VALIDATED"
    else:
        version.detail = f"validate failed: HTTP {response.status_code}"


def _print_summary(rows: Iterable[SeededVersion], api: str) -> None:
    """Print a fixture/document/version/status table and next-step hints."""
    rows = list(rows)
    headers = ("Fixture", "Document ID", "Version ID", "Status")
    table = [headers]
    for row in rows:
        table.append(
            (
                row.fixture,
                (row.document_id or "—")[:36],
                (row.version_id or "—")[:36],
                row.status + (f" ({row.detail})" if row.detail else ""),
            )
        )
    widths = [max(len(r[i]) for r in table) for i in range(len(headers))]
    sep = "  "
    print()
    for i, row in enumerate(table):
        print(sep.join(cell.ljust(widths[j]) for j, cell in enumerate(row)))
        if i == 0:
            print(sep.join("-" * w for w in widths))
    print()
    print("Next steps:")
    print(f"  - Open Orbital at http://localhost:5173 (it should hit {api})")
    print("  - Browse the catalog; v1 and v1_renamed share a sha256 — check the")
    print("    DUPLICATE_DETECTED row's `duplicate_of_version_id` field.")
    print("  - Versions in NEEDS_REVIEW are ready to validate or reject in the UI.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="seed_demo.py",
        description=(
            "Seed the local demo backend with a deterministic corpus. "
            "Idempotent: re-running against an already-seeded backend "
            "is harmless — duplicate uploads return DUPLICATE_DETECTED."
        ),
    )
    parser.add_argument(
        "--api",
        default=DEFAULT_API,
        help=f"Base URL of the running demo API (default: {DEFAULT_API}).",
    )
    parser.add_argument(
        "--validate-one",
        action="store_true",
        help=(
            "After seeding, POST /validate on one document so the "
            "knowledge-graph projection has something to render."
        ),
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help=(
            "Print the manual-reset recipe (rm -rf .kw-pipeline/ and "
            "restart the API) and exit. The script never deletes state "
            "itself."
        ),
    )
    args = parser.parse_args(argv)

    if args.reset:
        print(
            "To reset persistent demo state:\n"
            "  1. Stop the API (Ctrl+C in the uvicorn terminal).\n"
            "  2. rm -rf apps/api/.kw-pipeline/\n"
            "  3. Restart the API:\n"
            "       cd apps/api\n"
            "       KW_PERSISTENT=true uvicorn app.main:app --reload\n"
            "  4. Re-run this script."
        )
        return 0

    # Materialise PDF/DOCX on first run; cheap no-op afterwards.
    materialise_all()

    fixtures = [
        DEMO_DIR / "supplier_quality_policy_v1.txt",
        DEMO_DIR / "supplier_quality_policy_v2.txt",
        DEMO_DIR / "supplier_quality_policy_v1_renamed.txt",
        DEMO_DIR / PDF_NAME,
        DEMO_DIR / DOCX_NAME,
        DEMO_DIR / "empty.txt",
    ]
    missing = [p for p in fixtures if not p.exists()]
    if missing:
        names = ", ".join(p.name for p in missing)
        raise SystemExit(f"Missing demo fixtures: {names}")

    rows: list[SeededVersion] = []
    with httpx.Client(base_url=args.api, timeout=30.0) as client:
        _check_health(client)

        v1 = _upload(client, fixtures[0])
        rows.append(v1)
        # v2 chains onto v1's document family so the catalog shows a
        # multi-version document, not two parallel families.
        v2 = _upload(client, fixtures[1], document_id=v1.document_id)
        rows.append(v2)
        rows.append(_upload(client, fixtures[2]))  # duplicate of v1
        pdf = _upload(client, fixtures[3])
        rows.append(pdf)
        docx = _upload(client, fixtures[4])
        rows.append(docx)
        rows.append(_upload(client, fixtures[5]))  # empty.txt → HTTP 400

        # Drive a subset through extract + semantic so reviewers have
        # NEEDS_REVIEW work to look at. v2 is intentionally left at
        # STORED so the catalog has at least one pre-review version.
        for row in (v1, pdf, docx):
            if row.status == "STORED":
                _drive_to_needs_review(client, row)

        if args.validate_one:
            for row in (v1, pdf, docx):
                if row.status == "NEEDS_REVIEW":
                    _validate(client, row)
                    break

    _print_summary(rows, args.api)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
