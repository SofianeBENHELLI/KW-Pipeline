"""Coverage for ``GET /documents/{id}/versions/{v}/chunks`` —
the chunk-locations read route that powers the Phase 2 PDF viewer.

Asserts:

* Every parser-emitted section comes back as a :class:`ChunkLocation`
  with rects, page, heading, snippet, and ``document_hash`` from the
  version's SHA-256.
* ``source`` defaults to ``"parser"`` when no topic cites the chunk,
  and flips to ``"ai_extraction"`` when at least one
  :class:`DocumentTopic` lists the chunk in ``supporting_chunk_ids``.
* The route forwards ``page`` / ``source`` / ``min_confidence`` query
  filters straight through to the items list.
* 404 when the version does not exist or the raw extraction has not
  been persisted yet.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from fpdf import FPDF

from app.dependencies import build_services
from app.main import create_app
from app.schemas.document_topic import DOCUMENT_TOPIC_SCHEMA_VERSION, DocumentTopic
from app.services.parsers.pdf import PDF_CONTENT_TYPE


def _make_two_page_pdf() -> bytes:
    pdf = FPDF(format="letter")
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.cell(0, 7, "Battery aging considerations", new_x="LMARGIN", new_y="NEXT")
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.cell(0, 7, "Reviewer cadence and dissent record", new_x="LMARGIN", new_y="NEXT")
    buffer = io.BytesIO()
    pdf.output(buffer)
    return buffer.getvalue()


def _seed_extracted_pdf(client: TestClient) -> dict:
    """Upload + extract a small PDF, return the version dict."""
    upload = client.post(
        "/documents/upload",
        files={"file": ("policy.pdf", _make_two_page_pdf(), PDF_CONTENT_TYPE)},
    )
    assert upload.status_code == 200, upload.text
    version = upload.json()
    extract = client.post(f"/documents/{version['document_id']}/versions/{version['id']}/extract")
    assert extract.status_code == 200, extract.text
    return version


def _build_client(monkeypatch) -> tuple[TestClient, object]:
    monkeypatch.setenv("ALLOWED_CONTENT_TYPES", f"text/plain,{PDF_CONTENT_TYPE}")
    services = build_services()
    return TestClient(create_app(services=services)), services


def test_chunks_route_returns_parser_only_locations_with_rects(monkeypatch):
    client, _ = _build_client(monkeypatch)
    version = _seed_extracted_pdf(client)

    response = client.get(f"/documents/{version['document_id']}/versions/{version['id']}/chunks")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["document_id"] == version["document_id"]
    assert body["document_version_id"] == version["id"]
    assert body["document_hash"] == version["sha256"]
    assert body["parser_version"] == "0.2"
    assert body["schema_version"] == "v0.1"
    assert len(body["items"]) >= 2  # one section per page

    for item in body["items"]:
        assert item["document_hash"] == version["sha256"]
        assert item["source"] == "parser"
        assert item["confidence"] == 1.0
        assert item["summary"] is None
        assert item["topic_id"] is None
        assert item["rects"], f"chunk {item['chunk_id']} produced no rects"
        for rect in item["rects"]:
            assert 0.0 <= rect["x"] <= 1.0
            assert 0.0 <= rect["y"] <= 1.0
        assert item["page"] >= 1
        assert item["pipeline_version"].startswith("parser=0.2;")


def test_chunks_route_flips_source_to_ai_extraction_when_topic_cites_chunk(monkeypatch):
    client, services = _build_client(monkeypatch)
    version = _seed_extracted_pdf(client)
    # Grab the first chunk id from the freshly-persisted extraction so
    # the topic citation points at a real section.
    raw = services.documents.catalog.get_raw_extraction(version["id"])
    target_chunk_id = raw.sections[0].id

    services.document_topic_store.save_topics(
        [
            DocumentTopic(
                id="topic-battery",
                document_id=version["document_id"],
                version_id=version["id"],
                label="Battery aging",
                summary="Discussion of how cell capacity decays with time and use.",
                keywords=["battery", "aging"],
                confidence=0.87,
                schema_version=DOCUMENT_TOPIC_SCHEMA_VERSION,
                extracted_at=datetime.now(UTC),
                supporting_chunk_ids=[target_chunk_id],
            )
        ]
    )

    response = client.get(f"/documents/{version['document_id']}/versions/{version['id']}/chunks")
    assert response.status_code == 200, response.text
    items = response.json()["items"]

    enriched = next(i for i in items if i["chunk_id"] == target_chunk_id)
    assert enriched["source"] == "ai_extraction"
    assert enriched["topic_id"] == "topic-battery"
    assert enriched["topic_label"] == "Battery aging"
    assert enriched["summary"] is not None
    assert 0.86 < enriched["confidence"] < 0.88

    # Other chunks stay parser-only.
    others = [i for i in items if i["chunk_id"] != target_chunk_id]
    assert all(o["source"] == "parser" for o in others)


def test_chunks_route_picks_highest_confidence_topic_when_multiple_cite_one_chunk(
    monkeypatch,
):
    client, services = _build_client(monkeypatch)
    version = _seed_extracted_pdf(client)
    raw = services.documents.catalog.get_raw_extraction(version["id"])
    target_chunk_id = raw.sections[0].id

    services.document_topic_store.save_topics(
        [
            DocumentTopic(
                id="topic-low",
                document_id=version["document_id"],
                version_id=version["id"],
                label="Low-confidence theme",
                summary="A weak signal.",
                keywords=["weak"],
                confidence=0.3,
                schema_version=DOCUMENT_TOPIC_SCHEMA_VERSION,
                extracted_at=datetime.now(UTC),
                supporting_chunk_ids=[target_chunk_id],
            ),
            DocumentTopic(
                id="topic-high",
                document_id=version["document_id"],
                version_id=version["id"],
                label="High-confidence theme",
                summary="The strongest match.",
                keywords=["strong"],
                confidence=0.9,
                schema_version=DOCUMENT_TOPIC_SCHEMA_VERSION,
                extracted_at=datetime.now(UTC),
                supporting_chunk_ids=[target_chunk_id],
            ),
        ]
    )

    response = client.get(f"/documents/{version['document_id']}/versions/{version['id']}/chunks")
    items = response.json()["items"]
    enriched = next(i for i in items if i["chunk_id"] == target_chunk_id)
    assert enriched["topic_id"] == "topic-high"


def test_chunks_route_filters_by_page(monkeypatch):
    client, _ = _build_client(monkeypatch)
    version = _seed_extracted_pdf(client)

    response = client.get(
        f"/documents/{version['document_id']}/versions/{version['id']}/chunks",
        params={"page": 1},
    )
    items = response.json()["items"]
    assert items, "expected at least one chunk on page 1"
    assert {item["page"] for item in items} == {1}


def test_chunks_route_filters_by_source(monkeypatch):
    client, services = _build_client(monkeypatch)
    version = _seed_extracted_pdf(client)
    raw = services.documents.catalog.get_raw_extraction(version["id"])
    target_chunk_id = raw.sections[0].id

    services.document_topic_store.save_topics(
        [
            DocumentTopic(
                id="topic-ai",
                document_id=version["document_id"],
                version_id=version["id"],
                label="AI-flagged",
                summary="Sample summary.",
                keywords=["ai"],
                confidence=0.7,
                schema_version=DOCUMENT_TOPIC_SCHEMA_VERSION,
                extracted_at=datetime.now(UTC),
                supporting_chunk_ids=[target_chunk_id],
            )
        ]
    )

    only_ai = client.get(
        f"/documents/{version['document_id']}/versions/{version['id']}/chunks",
        params={"source": "ai_extraction"},
    ).json()["items"]
    assert {item["source"] for item in only_ai} == {"ai_extraction"}
    assert any(item["chunk_id"] == target_chunk_id for item in only_ai)

    only_parser = client.get(
        f"/documents/{version['document_id']}/versions/{version['id']}/chunks",
        params={"source": "parser"},
    ).json()["items"]
    assert {item["source"] for item in only_parser} == {"parser"}


def test_chunks_route_filters_by_min_confidence(monkeypatch):
    client, services = _build_client(monkeypatch)
    version = _seed_extracted_pdf(client)
    raw = services.documents.catalog.get_raw_extraction(version["id"])
    target_chunk_id = raw.sections[0].id

    services.document_topic_store.save_topics(
        [
            DocumentTopic(
                id="topic-medium",
                document_id=version["document_id"],
                version_id=version["id"],
                label="Medium",
                summary="Body.",
                keywords=["m"],
                confidence=0.5,
                schema_version=DOCUMENT_TOPIC_SCHEMA_VERSION,
                extracted_at=datetime.now(UTC),
                supporting_chunk_ids=[target_chunk_id],
            )
        ]
    )

    # Parser-only chunks return confidence=1.0 so a 0.8 floor must still
    # include them; only the medium-confidence AI chunk is filtered out.
    items = client.get(
        f"/documents/{version['document_id']}/versions/{version['id']}/chunks",
        params={"min_confidence": 0.8},
    ).json()["items"]
    assert all(item["confidence"] >= 0.8 for item in items)
    assert all(item["chunk_id"] != target_chunk_id for item in items)


def test_chunks_route_returns_404_for_unknown_version(monkeypatch):
    client, _ = _build_client(monkeypatch)
    version = _seed_extracted_pdf(client)

    response = client.get(f"/documents/{version['document_id']}/versions/does-not-exist/chunks")
    assert response.status_code == 404


def test_chunks_route_invariant_every_modern_chunk_has_at_least_one_rect(monkeypatch):
    """LLM-eval / Phase 5 contract: when parser_version is 0.2 or
    higher, every returned ChunkLocation must carry at least one
    rect with positive area. The viewer's tombstone branch only
    fires on the 0.1 fallback path, so a silent empty-rect row here
    would render as an invisible highlight — that is the failure
    mode this test is here to catch as the parser evolves.
    """
    client, _ = _build_client(monkeypatch)
    version = _seed_extracted_pdf(client)

    response = client.get(f"/documents/{version['document_id']}/versions/{version['id']}/chunks")
    body = response.json()
    assert body["parser_version"] >= "0.2"
    for item in body["items"]:
        assert item["rects"], (
            f"chunk {item['chunk_id']} returned with no rects under "
            f"parser_version {body['parser_version']}"
        )
        assert all(rect["width"] * rect["height"] > 0 for rect in item["rects"]), (
            f"chunk {item['chunk_id']} returned a zero-area rect"
        )


def test_chunks_route_returns_404_when_extraction_has_not_run(monkeypatch):
    monkeypatch.setenv("ALLOWED_CONTENT_TYPES", f"text/plain,{PDF_CONTENT_TYPE}")
    services = build_services()
    client = TestClient(create_app(services=services))
    upload = client.post(
        "/documents/upload",
        files={"file": ("policy.pdf", _make_two_page_pdf(), PDF_CONTENT_TYPE)},
    )
    version = upload.json()
    # Note: deliberately skip the /extract call so raw_extraction is missing.

    response = client.get(f"/documents/{version['document_id']}/versions/{version['id']}/chunks")
    assert response.status_code == 404
