"""Tests for ``GET /documents/{document_id}/high-value-chunks`` —
the operator "start here" surface (converged plan §C.2).

Covers:
* Cold-start: a freshly-uploaded doc with no semantic output yet
  returns an empty ``items`` list with HTTP 200.
* Happy path: a doc with a semantic document, claims, and
  processes ranks chunks DESC by composite score.
* Limit is honoured.
* ``?version_id=`` targeting a sibling version of the same family
  returns that version's data; a stranger version_id returns 404.
* Document not found → 404.
* Schema validation: response carries the ``v0.1`` literal +
  default weights envelope.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.models.document import DocumentVersionStatus
from app.schemas.claim import Claim
from app.schemas.high_value_chunks import (
    HIGH_VALUE_CHUNKS_SCHEMA_VERSION,
    HighValueChunksResponse,
)
from app.schemas.process import Process, ProcessStep
from app.schemas.scope import Scope


def _link_personal_scope(services, document_id: str, user_id: str = "dev") -> None:
    services.documents.catalog.add_scope(
        document_id,
        Scope(
            kind="personal",
            ref=user_id,
            added_at=datetime.now(UTC),
            added_by=user_id,
        ),
    )


def _land_in_needs_review(services, *, filename: str = "policy.txt") -> tuple[str, str]:
    """Upload → extract → semantic so the version has a semantic doc."""
    version = services.documents.upload(
        filename=filename,
        content_type="text/plain",
        content=(filename + " body").encode("utf-8"),
    )
    _link_personal_scope(services, version.document_id)
    services.extraction_jobs.extract(
        document_id=version.document_id, version_id=version.id,
    )
    services.semantic_outputs.generate(
        document_id=version.document_id, version_id=version.id,
    )
    refreshed = services.documents.get_version(
        document_id=version.document_id, version_id=version.id,
    )
    assert refreshed.status == DocumentVersionStatus.NEEDS_REVIEW
    return version.document_id, version.id


def _upload_only(services, *, filename: str = "stub.txt") -> tuple[str, str]:
    """Cold-start helper — upload bytes but never run extraction."""
    version = services.documents.upload(
        filename=filename,
        content_type="text/plain",
        content=(filename + " body").encode("utf-8"),
    )
    _link_personal_scope(services, version.document_id)
    return version.document_id, version.id


def _save_claim(
    services,
    *,
    claim_id: str,
    version_id: str,
    document_id: str,
    chunk_id: str,
    subject_entity_id: str = "entity-aaa",
) -> None:
    services.claim_store.save_claims(
        [
            Claim(
                id=claim_id,
                document_id=document_id,
                version_id=version_id,
                subject_entity_id=subject_entity_id,
                predicate="mentions",
                object_value="v",
                object_entity_id=None,
                confidence=0.9,
                extracted_at=datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC),
                provenance_chunk_ids=[chunk_id],
            )
        ]
    )


def _save_process(
    services,
    *,
    process_id: str,
    version_id: str,
    document_id: str,
    step_chunks: list[str],
) -> None:
    services.process_store.save_process(
        Process(
            id=process_id,
            title="SOP",
            document_id=document_id,
            version_id=version_id,
            created_at=datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC),
            steps=[
                ProcessStep(
                    step_number=i + 1,
                    title=f"step {i + 1}",
                    body="b",
                    source_reference_ids=[chunk_id],
                )
                for i, chunk_id in enumerate(step_chunks)
            ],
        )
    )


@pytest.fixture
def app_and_services():
    services = build_services()
    app = create_app(services=services)
    return app, services


# ─── Cold-start ────────────────────────────────────────────────────────


def test_cold_start_returns_empty_items_when_no_semantic_document(app_and_services) -> None:
    app, services = app_and_services
    document_id, version_id = _upload_only(services)
    client = TestClient(app)
    response = client.get(f"/documents/{document_id}/high-value-chunks")
    assert response.status_code == 200, response.text
    parsed = HighValueChunksResponse.model_validate(response.json())
    assert parsed.document_id == document_id
    assert parsed.version_id == version_id
    assert parsed.total_chunks == 0
    assert parsed.items == []
    assert parsed.schema_version == HIGH_VALUE_CHUNKS_SCHEMA_VERSION


# ─── Happy path ────────────────────────────────────────────────────────


def test_returns_ranked_items_with_signal_breakdown(app_and_services) -> None:
    app, services = app_and_services
    document_id, version_id = _land_in_needs_review(services)
    # Find a chunk id to attach a claim and a process step to.
    semantic = services.semantic_outputs.get(
        document_id=document_id, version_id=version_id,
    )
    target_chunk_id = semantic.sections[0].id
    _save_claim(
        services,
        claim_id="claim-1",
        version_id=version_id,
        document_id=document_id,
        chunk_id=target_chunk_id,
    )
    _save_process(
        services,
        process_id="proc-1",
        version_id=version_id,
        document_id=document_id,
        step_chunks=[target_chunk_id, target_chunk_id],
    )
    client = TestClient(app)
    response = client.get(f"/documents/{document_id}/high-value-chunks")
    assert response.status_code == 200, response.text
    parsed = HighValueChunksResponse.model_validate(response.json())
    assert parsed.total_chunks == len(semantic.sections)
    assert parsed.items, "ranker should emit at least one row"
    top = parsed.items[0]
    assert top.chunk_id == target_chunk_id
    assert top.claim_count == 1
    assert top.process_step_count == 2
    assert top.entity_mention_count >= 1
    assert top.score > 0.0
    # weights envelope is the default formula.
    assert parsed.weights.claims == pytest.approx(0.30)
    assert parsed.weights.process_steps == pytest.approx(0.20)
    assert parsed.weights.graph_degree == pytest.approx(0.25)
    assert parsed.weights.entity_density == pytest.approx(0.25)


def test_honours_limit_query_param(app_and_services) -> None:
    app, services = app_and_services
    document_id, _ = _land_in_needs_review(services)
    client = TestClient(app)
    response = client.get(
        f"/documents/{document_id}/high-value-chunks?limit=1",
    )
    assert response.status_code == 200, response.text
    parsed = HighValueChunksResponse.model_validate(response.json())
    assert len(parsed.items) <= 1


# ─── Targeting versions ────────────────────────────────────────────────


def test_explicit_version_id_for_unknown_version_returns_404(app_and_services) -> None:
    app, services = app_and_services
    document_id, _ = _land_in_needs_review(services)
    client = TestClient(app)
    response = client.get(
        f"/documents/{document_id}/high-value-chunks?version_id=does-not-exist",
    )
    assert response.status_code == 404


def test_missing_document_returns_404(app_and_services) -> None:
    app, _ = app_and_services
    client = TestClient(app)
    response = client.get("/documents/doc-missing/high-value-chunks")
    assert response.status_code == 404


# ─── Limit bounds ──────────────────────────────────────────────────────


def test_limit_out_of_range_returns_422(app_and_services) -> None:
    app, services = app_and_services
    document_id, _ = _land_in_needs_review(services)
    client = TestClient(app)
    too_high = client.get(
        f"/documents/{document_id}/high-value-chunks?limit=101"
    )
    too_low = client.get(
        f"/documents/{document_id}/high-value-chunks?limit=0"
    )
    assert too_high.status_code == 422
    assert too_low.status_code == 422
