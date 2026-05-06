"""Full demo-mode loader — populates a running backend with one rich corpus.

This is the "show every feature in one shot" companion to
``seed_demo.py``. Where the seed script ships a tiny, fast corpus
focused on the upload + duplicate path, this loader pushes a richer
dataset that exercises every user-visible feature of KW-Pipeline:

- documents, sections and chunks (every fixture is parsed + semantic-
  generated);
- multi-version lineage (v1 → v2 → v3 of the supplier onboarding policy,
  each validated in order, so v1/v2 land as ``SUPERSEDED`` and v3 is the
  current ``VALIDATED`` head);
- duplicate detection (the v1 bytes are re-uploaded under a new
  filename and surface as ``DUPLICATE_DETECTED``);
- topic clustering (fixtures are deliberately grouped into four topical
  clusters so the projector emits visible ``topic`` nodes and chunk-to-
  chunk semantic relations);
- hybrid taxonomy (an operator-imposed taxonomy YAML is wired via
  ``KW_TAXONOMY_PATH`` so ``GET /knowledge/taxonomy`` returns the merged
  imposed + computed categories);
- knowledge graph (every validated version is projected; the read
  routes return a populated ``KnowledgeGraphProjection``);
- similarity / linking (multiple documents share topic ids so
  ``GET /documents/{id}/similar`` returns ranked neighbours);
- review lifecycle (every fixture goes through ``extract → semantic
  → validate``; one fixture is rejected to demonstrate the rejection
  side of the review FSM);
- mixed parsers (text fixtures plus a PDF and a DOCX materialised from
  the existing ``_demo_fixtures`` helpers).

Usage
-----

    cd apps/api
    python scripts/load_demo_dataset.py [--api http://127.0.0.1:8000]
                                        [--reset]

Or, after ``pip install -e 'apps/api[test]'`` (or after running the
``./scripts/demo-backend.sh`` launcher once), the bundled console
script wraps the same defaults and points at the demo backend on
``127.0.0.1:8000``:

    .venv312/bin/kw-demo-load

The loader is HTTP-black-box — every interaction goes through the
public API on ``httpx``. That keeps it usable against the in-memory
backend, the SQLite-persistent backend (``KW_PERSISTENT=true``), and
any future remote demo deployment without code changes.

For the topic-clustering / knowledge-graph / taxonomy parts to actually
populate, the backend must run with ``KW_KNOWLEDGE_LAYER_ENABLED=true``;
the bundled ``kw-demo`` launcher already sets that. The loader logs a
warning when it detects an empty graph projection so a presenter is
not silently looking at a half-loaded demo.

Idempotency
-----------

Re-running the loader against an already-populated backend is safe but
not silent: duplicate detection fires on every fixture beyond the first
run, and the supersede flow re-asserts itself. To truly reset state,
stop the API, delete the persistent data dir, and restart it. Pass
``--reset`` to print the recipe (the script never deletes data on
disk itself).
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

# Make the existing demo-fixture helpers importable regardless of CWD.
# We reuse ``materialise_pdf`` / ``materialise_docx`` so the full-demo
# corpus also exercises the PDF and DOCX parsers without committing
# binary blobs into ``fixtures/full_demo/``.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from _demo_fixtures import (  # noqa: E402  (intentional sys.path tweak above)
    materialise_docx,
    materialise_pdf,
)

log = logging.getLogger(__name__)

DEFAULT_API = "http://127.0.0.1:8000"
FULL_DEMO_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "full_demo"

# Content-type lookup for fixtures. The upload route's allowlist is
# exact-match so we do not infer from the suffix at request time —
# we want the same MIME the UI would send.
_CONTENT_TYPES = {
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@dataclass
class LoadedVersion:
    """One row of the loader's summary table."""

    fixture: str
    cluster: str
    document_id: str | None = None
    version_id: str | None = None
    version_number: int | None = None
    status: str = "—"
    detail: str = ""


@dataclass
class LoadSummary:
    """Aggregate stats for the end-of-run report."""

    versions: list[LoadedVersion] = field(default_factory=list)
    duplicate_count: int = 0
    rejected_count: int = 0
    superseded_count: int = 0
    validated_count: int = 0
    needs_review_count: int = 0
    failed_count: int = 0
    graph_node_count: int = 0
    graph_edge_count: int = 0
    chunk_count: int = 0
    topic_count: int = 0
    similar_pairs: int = 0
    taxonomy_imposed: int = 0
    taxonomy_computed: int = 0


# ─── Fixture catalogue ────────────────────────────────────────────────
#
# Each entry pairs a fixture name with the cluster label used in the
# summary table. The cluster labels are presentation-only — the actual
# topic clustering happens server-side from the fixture text — but
# grouping them here keeps the loader output legible.
TEXT_FIXTURES: list[tuple[str, str]] = [
    ("quality_iso9001_handbook.txt", "quality"),
    ("quality_audit_findings_2026q1.txt", "quality"),
    ("quality_corrective_action_log.txt", "quality"),
    ("supplier_qualification_checklist.txt", "suppliers"),
    ("customer_renewal_brief.txt", "customer-success"),
    ("customer_success_playbook.txt", "customer-success"),
    ("engineering_change_request_4471.txt", "engineering"),
    ("engineering_design_review_minutes.txt", "engineering"),
]

# Multi-version family — uploaded as the same document family so the
# supersede flow lights up. Order matters: v1 is uploaded first, then
# validated, then v2 is appended, validated (which moves v1 to
# SUPERSEDED), then v3.
SUPPLIER_FAMILY: list[tuple[str, str]] = [
    ("supplier_onboarding_policy_v1.txt", "suppliers"),
    ("supplier_onboarding_policy_v2.txt", "suppliers"),
    ("supplier_onboarding_policy_v3.txt", "suppliers"),
]

# Fixture intentionally rejected so the rejection FSM transition is
# exercised. The choice is arbitrary; engineering-design-review is a
# convenient candidate because it has no downstream dependencies in the
# rest of the demo flow.
REJECTED_FIXTURE = "engineering_design_review_minutes.txt"


# ─── HTTP helpers ─────────────────────────────────────────────────────


def _content_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix not in _CONTENT_TYPES:
        raise ValueError(f"Unsupported demo fixture suffix: {path.name}")
    return _CONTENT_TYPES[suffix]


def _check_health(client: httpx.Client) -> None:
    try:
        response = client.get("/health", timeout=5.0)
    except httpx.HTTPError as exc:
        raise SystemExit(
            f"Cannot reach the demo backend at {client.base_url}.\n"
            "Start it first, e.g.:\n"
            "    ./scripts/demo-backend.sh\n"
            "or\n"
            "    cd apps/api\n"
            "    KW_PERSISTENT=true KW_KNOWLEDGE_LAYER_ENABLED=true \\\n"
            "        uvicorn app.main:app --reload\n"
            f"Original error: {exc}"
        ) from exc
    if response.status_code != 200:
        raise SystemExit(f"Health check returned HTTP {response.status_code}: {response.text}")


def _upload(
    client: httpx.Client,
    path: Path,
    *,
    document_id: str | None = None,
    cluster: str,
    fixture_label: str | None = None,
) -> LoadedVersion:
    content_type = _content_type_for(path)
    files = {"file": (fixture_label or path.name, path.read_bytes(), content_type)}
    params = {"document_id": document_id} if document_id else None
    response = client.post("/documents/upload", files=files, params=params)
    if response.status_code != 200:
        return LoadedVersion(
            fixture=fixture_label or path.name,
            cluster=cluster,
            status=f"HTTP {response.status_code}",
            detail=response.text.strip()[:140],
        )
    body = response.json()
    return LoadedVersion(
        fixture=fixture_label or path.name,
        cluster=cluster,
        document_id=body["document_id"],
        version_id=body["id"],
        version_number=body.get("version_number"),
        status=body["status"],
    )


def _drive_to_needs_review(client: httpx.Client, version: LoadedVersion) -> None:
    """Run extract + semantic so the version lands in NEEDS_REVIEW."""
    if version.document_id is None or version.version_id is None:
        return
    base = f"/documents/{version.document_id}/versions/{version.version_id}"
    extract = client.post(f"{base}/extract")
    if extract.status_code != 200:
        version.detail = f"extract failed: HTTP {extract.status_code}"
        version.status = "FAILED"
        return
    semantic = client.post(f"{base}/semantic")
    if semantic.status_code != 200:
        version.detail = f"semantic failed: HTTP {semantic.status_code}"
        version.status = "FAILED"
        return
    version.status = "NEEDS_REVIEW"


def _validate(
    client: httpx.Client,
    version: LoadedVersion,
    *,
    note: str = "demo loader: validated",
) -> None:
    if version.document_id is None or version.version_id is None:
        return
    response = client.post(
        f"/documents/{version.document_id}/versions/{version.version_id}/validate",
        json={"reviewer_note": note},
    )
    if response.status_code == 200:
        version.status = "VALIDATED"
    else:
        version.detail = f"validate failed: HTTP {response.status_code}"


def _reject(
    client: httpx.Client,
    version: LoadedVersion,
    *,
    note: str = "demo loader: rejected to exercise the rejection path",
) -> None:
    if version.document_id is None or version.version_id is None:
        return
    response = client.post(
        f"/documents/{version.document_id}/versions/{version.version_id}/reject",
        json={"reviewer_note": note},
    )
    if response.status_code == 200:
        version.status = "REJECTED"
    else:
        version.detail = f"reject failed: HTTP {response.status_code}"


# ─── Phases ───────────────────────────────────────────────────────────


def _load_topic_corpus(client: httpx.Client, summary: LoadSummary) -> None:
    """Upload + extract + semantic + validate every standalone fixture."""
    for filename, cluster in TEXT_FIXTURES:
        path = FULL_DEMO_DIR / filename
        row = _upload(client, path, cluster=cluster)
        summary.versions.append(row)
        if row.status != "STORED":
            continue
        _drive_to_needs_review(client, row)
        if row.status != "NEEDS_REVIEW":
            continue
        if filename == REJECTED_FIXTURE:
            _reject(client, row)
        else:
            _validate(client, row)


def _load_supplier_family(client: httpx.Client, summary: LoadSummary) -> None:
    """Upload v1 → v2 → v3 into one document family, validating in order.

    Each validate call after v1 implicitly moves the prior validated
    sibling to ``SUPERSEDED`` (review_service._maybe_supersede_prior_validated).
    The summary table reflects the final post-supersede state.
    """
    parent_document_id: str | None = None
    family_versions: list[LoadedVersion] = []

    for filename, cluster in SUPPLIER_FAMILY:
        path = FULL_DEMO_DIR / filename
        row = _upload(client, path, document_id=parent_document_id, cluster=cluster)
        summary.versions.append(row)
        family_versions.append(row)
        if row.document_id and parent_document_id is None:
            parent_document_id = row.document_id
        if row.status != "STORED":
            continue
        _drive_to_needs_review(client, row)
        if row.status != "NEEDS_REVIEW":
            continue
        _validate(
            client,
            row,
            note=f"demo loader: validated {filename} into family {parent_document_id}",
        )

    # After all three are validated, refresh the rows so the summary
    # reflects the SUPERSEDED state of v1 and v2. We re-read the document
    # once and overlay the latest status onto our local rows.
    if parent_document_id is None:
        return
    response = client.get(f"/documents/{parent_document_id}")
    if response.status_code != 200:
        return
    by_id = {v["id"]: v for v in response.json().get("versions", [])}
    for row in family_versions:
        if row.version_id and row.version_id in by_id:
            row.status = by_id[row.version_id]["status"]


def _load_duplicate(client: httpx.Client, summary: LoadSummary) -> None:
    """Re-upload the v1 bytes under a different filename to fire DUPLICATE_DETECTED."""
    path = FULL_DEMO_DIR / "supplier_onboarding_policy_v1.txt"
    row = _upload(
        client,
        path,
        cluster="suppliers",
        fixture_label="supplier_onboarding_policy_v1_renamed.txt",
    )
    summary.versions.append(row)


def _load_binary_fixtures(client: httpx.Client, summary: LoadSummary) -> None:
    """Materialise + upload the PDF and DOCX so the binary parsers run too."""
    pdf_path = FULL_DEMO_DIR / "engineering_change_request.pdf"
    docx_path = FULL_DEMO_DIR / "weekly_quality_review.docx"
    materialise_pdf(pdf_path)
    materialise_docx(docx_path)

    for path, cluster in [(pdf_path, "engineering"), (docx_path, "quality")]:
        row = _upload(client, path, cluster=cluster)
        summary.versions.append(row)
        if row.status != "STORED":
            continue
        _drive_to_needs_review(client, row)
        if row.status != "NEEDS_REVIEW":
            continue
        _validate(client, row, note=f"demo loader: validated {path.name}")


# ─── Post-load probes ─────────────────────────────────────────────────


def _aggregate_status_counts(summary: LoadSummary) -> None:
    for row in summary.versions:
        if row.status == "DUPLICATE_DETECTED":
            summary.duplicate_count += 1
        elif row.status == "REJECTED":
            summary.rejected_count += 1
        elif row.status == "SUPERSEDED":
            summary.superseded_count += 1
        elif row.status == "VALIDATED":
            summary.validated_count += 1
        elif row.status == "NEEDS_REVIEW":
            summary.needs_review_count += 1
        elif row.status == "FAILED" or row.status.startswith("HTTP "):
            summary.failed_count += 1


def _probe_knowledge_graph(client: httpx.Client, summary: LoadSummary) -> None:
    """Walk ``/knowledge/graph`` once to populate the summary's KG counters."""
    cursor: str | None = None
    seen_chunks: set[str] = set()
    seen_topics: set[str] = set()
    while True:
        params: dict[str, Any] = {"limit": 200}
        if cursor:
            params["cursor"] = cursor
        response = client.get("/knowledge/graph", params=params)
        if response.status_code != 200:
            log.warning(
                "knowledge graph probe failed (HTTP %s); is KW_KNOWLEDGE_LAYER_ENABLED=true?",
                response.status_code,
            )
            return
        body = response.json()
        nodes = body.get("nodes") or []
        edges = body.get("edges") or []
        summary.graph_node_count += len(nodes)
        summary.graph_edge_count += len(edges)
        for node in nodes:
            kind = node.get("kind")
            if kind == "chunk":
                seen_chunks.add(node.get("id", ""))
            elif kind == "topic":
                seen_topics.add(node.get("id", ""))
        cursor = body.get("next_cursor")
        if not cursor:
            break
    summary.chunk_count = len(seen_chunks)
    summary.topic_count = len(seen_topics)


def _probe_taxonomy(client: httpx.Client, summary: LoadSummary) -> None:
    response = client.get("/knowledge/taxonomy")
    if response.status_code != 200:
        return
    for category in response.json().get("categories", []):
        if category.get("source") == "imposed":
            summary.taxonomy_imposed += 1
        elif category.get("source") == "computed":
            summary.taxonomy_computed += 1


def _probe_similarity(client: httpx.Client, summary: LoadSummary) -> None:
    """For every validated version, hit ``/similar`` and count non-empty replies."""
    seen_documents: set[str] = set()
    for row in summary.versions:
        if not row.document_id or row.status != "VALIDATED":
            continue
        if row.document_id in seen_documents:
            continue
        seen_documents.add(row.document_id)
        response = client.get(f"/documents/{row.document_id}/similar", params={"k": 5})
        if response.status_code != 200:
            continue
        results = response.json().get("results") or []
        summary.similar_pairs += len(results)


# ─── Reporting ────────────────────────────────────────────────────────


def _print_summary(summary: LoadSummary, api: str) -> None:
    rows: list[tuple[str, str, str, str, str]] = [
        ("Cluster", "Fixture", "Document", "Version", "Status"),
    ]
    for row in summary.versions:
        rows.append(
            (
                row.cluster,
                row.fixture,
                (row.document_id or "—")[:36],
                f"v{row.version_number}" if row.version_number else "—",
                row.status + (f" ({row.detail})" if row.detail else ""),
            )
        )
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    sep = "  "
    print()
    print("KW-Pipeline full demo dataset loaded")
    print("=" * 36)
    for i, row in enumerate(rows):
        print(sep.join(cell.ljust(widths[j]) for j, cell in enumerate(row)))
        if i == 0:
            print(sep.join("-" * w for w in widths))

    print()
    print("Lifecycle counters")
    print(f"  validated:    {summary.validated_count}")
    print(f"  superseded:   {summary.superseded_count}")
    print(f"  needs_review: {summary.needs_review_count}")
    print(f"  rejected:     {summary.rejected_count}")
    print(f"  duplicates:   {summary.duplicate_count}")
    print(f"  failed:       {summary.failed_count}")

    print()
    print("Knowledge layer")
    print(f"  graph nodes:        {summary.graph_node_count}")
    print(f"  graph edges:        {summary.graph_edge_count}")
    print(f"  chunks:             {summary.chunk_count}")
    print(f"  topic clusters:     {summary.topic_count}")
    print(f"  similar-doc edges:  {summary.similar_pairs}")
    print(f"  taxonomy (imposed): {summary.taxonomy_imposed}")
    print(f"  taxonomy (computed): {summary.taxonomy_computed}")
    if summary.graph_node_count == 0:
        print()
        print(
            "  ⚠ Empty graph projection. Make sure the backend was started\n"
            "    with KW_KNOWLEDGE_LAYER_ENABLED=true (the bundled\n"
            "    ./scripts/demo-backend.sh launcher and `kw-demo` already do)."
        )

    print()
    print("Next steps")
    print(f"  - Open the Orbital UI and point it at {api}.")
    print("  - Browse /documents — note the supplier-onboarding-policy family")
    print("    with v1/v2 SUPERSEDED and v3 VALIDATED.")
    print("  - Open /documents/{id}/graph for any validated doc to see chunks,")
    print("    topics, and chunk-to-chunk semantic relations.")
    print("  - Hit /knowledge/taxonomy — imposed (YAML) and computed (clusters)")
    print("    are merged with the operator's YAML winning on id collisions.")
    print("  - The renamed v1 file shows the duplicate-detection path.")


# ─── Entry point ──────────────────────────────────────────────────────


def _missing_fixtures() -> list[Path]:
    expected = (
        [FULL_DEMO_DIR / name for name, _ in TEXT_FIXTURES]
        + [FULL_DEMO_DIR / name for name, _ in SUPPLIER_FAMILY]
        + [FULL_DEMO_DIR / "taxonomy.yaml"]
    )
    return [path for path in expected if not path.exists()]


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="load_demo_dataset.py",
        description=(
            "Populate a running KW-Pipeline backend with the full demo "
            "corpus — documents, versions, lineage, duplicate detection, "
            "topic clustering, hybrid taxonomy, knowledge graph, and the "
            "review lifecycle (validate + reject)."
        ),
    )
    parser.add_argument(
        "--api",
        default=DEFAULT_API,
        help=f"Base URL of the running demo API (default: {DEFAULT_API}).",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help=(
            "Print the manual reset recipe and exit. The script never deletes data on disk itself."
        ),
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.reset:
        print(
            "To reset persistent demo state:\n"
            "  1. Stop the API (Ctrl+C in the uvicorn terminal).\n"
            "  2. rm -rf apps/api/.kw-pipeline/\n"
            "  3. Restart the API:\n"
            "       ./scripts/demo-backend.sh\n"
            "  4. Re-run this loader."
        )
        return 0

    missing = _missing_fixtures()
    if missing:
        names = ", ".join(p.name for p in missing)
        raise SystemExit(f"Missing demo fixtures: {names}")

    summary = LoadSummary()
    with httpx.Client(base_url=args.api, timeout=60.0) as client:
        _check_health(client)
        _load_topic_corpus(client, summary)
        _load_supplier_family(client, summary)
        _load_duplicate(client, summary)
        _load_binary_fixtures(client, summary)
        _aggregate_status_counts(summary)
        _probe_knowledge_graph(client, summary)
        _probe_taxonomy(client, summary)
        _probe_similarity(client, summary)

    _print_summary(summary, args.api)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main(sys.argv[1:]))
