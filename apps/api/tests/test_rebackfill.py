"""Coverage for ``app.rebackfill`` — the legacy-PDF re-extraction CLI.

Asserts:

* Dry-run reports the eligible plan without mutating any store.
* Real run flips ``parser_version`` 0.1 rows to 0.2 with rects populated.
* Already-0.2 rows are skipped (idempotent on re-run).
* Previously-VALIDATED rows are demoted back to ``NEEDS_REVIEW`` with
  the documented audit note.
* Persisted claims and document-topics are deleted so their stale
  ``supporting_chunk_ids`` no longer point at vanished sections.
* ``--limit`` and ``--document-id`` filters narrow the work set.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from fpdf import FPDF

from app.dependencies import build_services
from app.main import create_app
from app.models.document import DocumentVersionStatus
from app.rebackfill import _DEMOTE_NOTE, run_rebackfill
from app.schemas.claim import CLAIM_SCHEMA_VERSION, Claim
from app.schemas.document_topic import DOCUMENT_TOPIC_SCHEMA_VERSION, DocumentTopic
from app.schemas.extraction import RawExtraction, RawSection, SourceReference
from app.services.parsers.pdf import PDF_CONTENT_TYPE


def _make_pdf(text: str = "First paragraph") -> bytes:
    pdf = FPDF(format="letter")
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.cell(0, 7, text, new_x="LMARGIN", new_y="NEXT")
    buffer = io.BytesIO()
    pdf.output(buffer)
    return buffer.getvalue()


def _seed_legacy_pdf(services, *, distinct_marker: str = "First paragraph") -> tuple[str, str]:
    """Upload a PDF and force a parser_version 0.1 extraction on disk.

    The fastest way to fabricate a legacy row without restoring a stale
    parser binary is to write the v0.1 wire shape (one section per page,
    no rects) directly through the catalog. The route layer is bypassed
    because the catalog is the source of truth that ``rebackfill`` reads.

    ``distinct_marker`` flows into the PDF body so successive calls
    produce different SHA-256 hashes and therefore distinct document
    families (otherwise the dedup path would alias every seed into
    the first one's ``document_id``).
    """
    version = services.documents.upload(
        filename="policy.pdf",
        content_type=PDF_CONTENT_TYPE,
        content=_make_pdf(distinct_marker),
    )

    legacy_ref = SourceReference(
        document_version_id=version.id,
        section_id="page-1",
        page_number=1,
        snippet="First paragraph",
    )
    legacy_section = RawSection(
        id="page-1",
        heading="Page 1",
        text="First paragraph",
        source_reference_ids=[legacy_ref.id],
        page_number=1,
        parser_metadata={"page_number": "1"},
    )
    legacy_extraction = RawExtraction(
        document_version_id=version.id,
        parser_name="pdf",
        parser_version="0.1",
        text="First paragraph",
        sections=[legacy_section],
        source_references=[legacy_ref],
    )
    services.documents.catalog.save_raw_extraction(version.id, legacy_extraction)
    return version.document_id, version.id


def test_dry_run_reports_plan_without_writing(tmp_path):
    services = build_services()
    document_id, version_id = _seed_legacy_pdf(services)

    result = run_rebackfill(services=services, dry_run=True)

    assert result.eligible_versions == 1
    assert [p.version_id for p in result.plan] == [version_id]
    assert result.rebackfilled == []
    assert result.demoted == []
    # The on-disk row stays on 0.1 because dry-run does not write.
    raw = services.documents.catalog.get_raw_extraction(version_id)
    assert raw.parser_version == "0.1"
    assert raw.sections[0].id == "page-1"


def test_real_run_rewrites_to_parser_version_0_2_with_rects():
    services = build_services()
    document_id, version_id = _seed_legacy_pdf(services)

    result = run_rebackfill(services=services)

    assert result.rebackfilled == [version_id]
    raw = services.documents.catalog.get_raw_extraction(version_id)
    assert raw.parser_version == "0.2"
    # New section ids follow the page-{N}-sec-{M} convention.
    assert all(s.id.startswith("page-1-sec-") for s in raw.sections)
    # Every rebuilt source reference now carries at least one rect.
    assert all(ref.rects for ref in raw.source_references)


def test_already_0_2_versions_are_skipped_on_rerun():
    services = build_services()
    _seed_legacy_pdf(services)

    first = run_rebackfill(services=services)
    second = run_rebackfill(services=services)

    assert len(first.rebackfilled) == 1
    assert second.eligible_versions == 0
    assert second.rebackfilled == []


def test_previously_validated_row_is_demoted_to_needs_review(monkeypatch):
    """Drive a real version all the way through VALIDATED via the HTTP
    routes (so the FSM transitions match production), then overwrite the
    persisted raw_extraction with a v0.1 payload to simulate a legacy
    row. The catalog state is now ``VALIDATED`` + ``parser_version=0.1``,
    which is exactly what the backfill needs to demote.
    """
    monkeypatch.setenv("ALLOWED_CONTENT_TYPES", f"text/plain,{PDF_CONTENT_TYPE}")
    services = build_services()
    client = TestClient(create_app(services=services))

    upload = client.post(
        "/documents/upload",
        files={"file": ("policy.pdf", _make_pdf("Body"), PDF_CONTENT_TYPE)},
    )
    assert upload.status_code == 200, upload.text
    version = upload.json()
    document_id, version_id = version["document_id"], version["id"]
    assert (
        client.post(
            f"/documents/{document_id}/versions/{version_id}/extract"
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/documents/{document_id}/versions/{version_id}/semantic"
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/documents/{document_id}/versions/{version_id}/validate"
        ).status_code
        == 200
    )

    # Overwrite the raw_extraction with a v0.1 payload so rebackfill
    # considers the row legacy.
    legacy_ref = SourceReference(
        document_version_id=version_id,
        section_id="page-1",
        page_number=1,
        snippet="Body",
    )
    services.documents.catalog.save_raw_extraction(
        version_id,
        RawExtraction(
            document_version_id=version_id,
            parser_name="pdf",
            parser_version="0.1",
            text="Body",
            sections=[
                RawSection(
                    id="page-1",
                    heading="Page 1",
                    text="Body",
                    source_reference_ids=[legacy_ref.id],
                    page_number=1,
                    parser_metadata={"page_number": "1"},
                )
            ],
            source_references=[legacy_ref],
        ),
    )

    result = run_rebackfill(services=services)

    assert result.demoted == [version_id]
    refreshed = services.documents.get_version(
        document_id=document_id, version_id=version_id
    )
    assert refreshed.status is DocumentVersionStatus.NEEDS_REVIEW
    assert refreshed.reviewer_note == _DEMOTE_NOTE


def test_rebackfill_deletes_stale_claims_and_topics():
    services = build_services()
    document_id, version_id = _seed_legacy_pdf(services)
    # Seed one claim + one topic citing the OLD ``page-1`` section.
    services.claim_store.save_claims(
        [
            Claim(
                id="claim-1",
                document_id=document_id,
                version_id=version_id,
                subject_entity_id="entity-x",
                predicate="is_a",
                object_value="policy",
                confidence=0.8,
                schema_version=CLAIM_SCHEMA_VERSION,
                extracted_at=datetime.now(UTC),
                provenance_chunk_ids=["page-1"],
            )
        ]
    )
    services.document_topic_store.save_topics(
        [
            DocumentTopic(
                id="topic-1",
                document_id=document_id,
                version_id=version_id,
                label="Policy intro",
                summary="Old summary citing the legacy chunk id.",
                keywords=["policy"],
                confidence=0.7,
                schema_version=DOCUMENT_TOPIC_SCHEMA_VERSION,
                extracted_at=datetime.now(UTC),
                supporting_chunk_ids=["page-1"],
            )
        ]
    )

    run_rebackfill(services=services)

    remaining_topics, _ = services.document_topic_store.list_for_document(document_id)
    assert remaining_topics == []
    # No public ``list_for_version`` on the claim store; the secondary
    # delete returns 0 on a clean store, which proves the row is gone.
    assert services.claim_store.delete_for_version(version_id) == 0


def test_limit_caps_eligible_set():
    services = build_services()
    _seed_legacy_pdf(services, distinct_marker="alpha doc one")
    _seed_legacy_pdf(services, distinct_marker="beta doc two")

    result = run_rebackfill(services=services, limit=1)

    assert len(result.rebackfilled) == 1
    # The other legacy row stays on 0.1 — the cap is respected.
    legacy_left = [
        d
        for d in services.documents.list_documents()
        for v in d.versions
        if services.documents.catalog.get_raw_extraction(v.id).parser_version == "0.1"
    ]
    assert len(legacy_left) == 1


def test_document_id_filter_narrows_work_set():
    services = build_services()
    doc_a, version_a = _seed_legacy_pdf(services, distinct_marker="alpha doc")
    doc_b, version_b = _seed_legacy_pdf(services, distinct_marker="beta doc")
    assert doc_a != doc_b, "seed fixture must produce distinct documents"

    result = run_rebackfill(services=services, document_id=doc_a)

    assert result.rebackfilled == [version_a]
    assert (
        services.documents.catalog.get_raw_extraction(version_b).parser_version
        == "0.1"
    )
