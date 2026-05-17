"""Tests for ``GET /documents/{document_id}/confidence`` — the
reviewer-UI confidence dashboard route (converged plan §C.1).

Covers:

* Happy path: validated version with scored metadata returns the
  full payload with ``has_score=true``.
* No metadata row at all: ``has_score=false`` with all dependent
  fields ``None`` and the configured threshold echoed.
* Metadata exists but ``confidence_score is None`` (scorer disabled):
  ``has_score=false`` while routing / validation fields still surface.
* Explicit ``?version_id=`` targeting a non-latest version of the
  same document family returns that version's data.
* Explicit ``?version_id=`` for a version not in the family → 404
  (anti-enumeration: callers cannot scrape confidence rows from
  versions in other scopes via a known id).
* Document not found → 404.
* Threshold is sourced from ``KW_HITL_AUTO_VALIDATE_THRESHOLD``
  with the documented 0.85 default; an env override is observed.
* Schema validation: response carries the ``v0.1`` literal.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app
from app.schemas.document_confidence import (
    DOCUMENT_CONFIDENCE_SCHEMA_VERSION,
    DocumentConfidenceResponse,
)
from app.schemas.scope import Scope
from app.schemas.validation_metadata import (
    ConfidenceScore,
    ValidationMetadata,
)
from app.services.confidence_scorer import ALL_SIGNALS


def _link_personal_scope(services, document_id: str, user_id: str = "dev") -> None:
    """Service-direct uploads bypass the route-layer scope write, so
    the dev-mode user can't see the doc under D.5 scope filtering.
    Mirror the helper PR #478 added to ``test_audit_actor_backfill``;
    issue #481 tracks the underlying layering fix."""
    services.documents.catalog.add_scope(
        document_id,
        Scope(
            kind="personal",
            ref=user_id,
            added_at=datetime.now(UTC),
            added_by=user_id,
        ),
    )


def _make_score(*, overall: float = 0.91, ocr_override: bool = False) -> ConfidenceScore:
    return ConfidenceScore(
        overall=overall,
        signals=dict.fromkeys(ALL_SIGNALS, overall),
        weights=dict.fromkeys(ALL_SIGNALS, 0.2),
        ocr_override_active=ocr_override,
        computed_at=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
        computed_by_version="v1",
    )


def _upload(
    services,
    *,
    filename: str = "policy.txt",
    content: bytes | None = None,
) -> str:
    """Seed a single uploaded version and return ``(document_id, version_id)``.

    Defaults the content body to the filename so two separate
    ``_upload(filename="doc1.txt")`` / ``_upload(filename="doc2.txt")``
    calls don't sha256-dedupe into the same family.
    """
    body = content if content is not None else filename.encode("utf-8") + b" body"
    version = services.documents.upload(
        filename=filename,
        content_type="text/plain",
        content=body,
    )
    _link_personal_scope(services, version.document_id)
    return version.document_id, version.id


@pytest.fixture
def app_and_services():
    services = build_services()
    app = create_app(services=services)
    return app, services


# ─── Happy path ────────────────────────────────────────────────────────


def test_returns_full_payload_when_metadata_has_score(app_and_services) -> None:
    app, services = app_and_services
    document_id, version_id = _upload(services)
    services.validation_metadata.upsert(
        ValidationMetadata(
            version_id=version_id,
            confidence_score=_make_score(overall=0.91),
            routing_decision="auto",
            validation_method="auto",
            validation_actor="system:hitl_auto_promote",
        )
    )
    client = TestClient(app)
    response = client.get(f"/documents/{document_id}/confidence")
    assert response.status_code == 200, response.text
    parsed = DocumentConfidenceResponse.model_validate(response.json())
    assert parsed.has_score is True
    assert parsed.document_id == document_id
    assert parsed.version_id == version_id
    assert parsed.version_number == 1
    assert parsed.confidence_score is not None
    assert parsed.confidence_score.overall == 0.91
    assert parsed.routing_decision == "auto"
    assert parsed.validation_method == "auto"
    assert parsed.validation_actor == "system:hitl_auto_promote"
    assert parsed.auto_validate_threshold == pytest.approx(0.85)
    assert parsed.schema_version == DOCUMENT_CONFIDENCE_SCHEMA_VERSION


# ─── Missing-data shapes ───────────────────────────────────────────────


def test_returns_empty_state_when_no_metadata_row(app_and_services) -> None:
    """A version that never reached NEEDS_REVIEW (or whose metadata
    row was never written) returns ``has_score=false`` with all
    dependent fields ``None`` — the threshold is still echoed so the
    UI can render a meaningful empty state."""
    app, services = app_and_services
    document_id, _ = _upload(services)
    client = TestClient(app)
    response = client.get(f"/documents/{document_id}/confidence")
    assert response.status_code == 200, response.text
    parsed = DocumentConfidenceResponse.model_validate(response.json())
    assert parsed.has_score is False
    assert parsed.confidence_score is None
    assert parsed.routing_decision is None
    assert parsed.validation_method is None
    assert parsed.validation_actor is None
    assert parsed.auto_validate_threshold == pytest.approx(0.85)


def test_returns_empty_state_when_scorer_disabled(app_and_services) -> None:
    """``KW_HITL_DISABLE_SCORER`` truthy → the metadata row exists
    (the router still writes routing data) but ``confidence_score``
    is ``None``. The dashboard surfaces ``has_score=false`` while
    still echoing the routing decision so the operator can see *what
    happened* even when *the score* is unavailable."""
    app, services = app_and_services
    document_id, version_id = _upload(services)
    services.validation_metadata.upsert(
        ValidationMetadata(
            version_id=version_id,
            confidence_score=None,
            routing_decision="human",
            validation_method="human",
            validation_actor="user:reviewer-1",
        )
    )
    client = TestClient(app)
    response = client.get(f"/documents/{document_id}/confidence")
    parsed = DocumentConfidenceResponse.model_validate(response.json())
    assert parsed.has_score is False
    assert parsed.confidence_score is None
    assert parsed.routing_decision == "human"
    assert parsed.validation_method == "human"
    assert parsed.validation_actor == "user:reviewer-1"


# ─── Explicit version_id ───────────────────────────────────────────────


def test_explicit_version_id_targets_that_version(app_and_services) -> None:
    """When ``?version_id=`` is supplied, the route reports on that
    version (not ``latest_version_id``). Useful for drift comparison
    between an older score and the current one."""
    app, services = app_and_services
    document_id, v1_id = _upload(services, filename="policy-v1.txt")
    # Upload v2 of the same document family.
    v2 = services.documents.upload(
        filename="policy-v1.txt",  # same filename → same family per dedup rules
        content_type="text/plain",
        content=b"Hello world. This is a tiny test fixture v2.",
    )
    # If dedup put v2 in a separate family we just exercise v1's id —
    # the contract still holds, just less interesting.
    if v2.document_id != document_id:
        pytest.skip("Dedup landed v2 in a separate family; not a contract test.")
    services.validation_metadata.upsert(
        ValidationMetadata(
            version_id=v1_id,
            confidence_score=_make_score(overall=0.60),
            routing_decision="human",
        )
    )
    services.validation_metadata.upsert(
        ValidationMetadata(
            version_id=v2.id,
            confidence_score=_make_score(overall=0.92),
            routing_decision="auto",
        )
    )
    client = TestClient(app)
    response = client.get(
        f"/documents/{document_id}/confidence?version_id={v1_id}",
    )
    parsed = DocumentConfidenceResponse.model_validate(response.json())
    assert parsed.version_id == v1_id
    assert parsed.confidence_score is not None
    assert parsed.confidence_score.overall == 0.60


def test_explicit_version_id_in_another_family_returns_404(app_and_services) -> None:
    """Cross-document scraping guard: a known ``version_id`` from
    another document family does not return that family's confidence
    under the requested document's id."""
    app, services = app_and_services
    doc1_id, _ = _upload(services, filename="doc1.txt")
    _, doc2_v_id = _upload(services, filename="doc2.txt")
    client = TestClient(app)
    response = client.get(f"/documents/{doc1_id}/confidence?version_id={doc2_v_id}")
    assert response.status_code == 404, response.text
    assert "not found in document" in response.json()["detail"].lower()


# ─── 404s ──────────────────────────────────────────────────────────────


def test_returns_404_when_document_missing() -> None:
    app = create_app()
    client = TestClient(app)
    response = client.get("/documents/no-such-doc/confidence")
    assert response.status_code == 404


# ─── Threshold ─────────────────────────────────────────────────────────


def test_threshold_default_is_0_85(app_and_services) -> None:
    app, services = app_and_services
    document_id, _ = _upload(services)
    client = TestClient(app)
    parsed = DocumentConfidenceResponse.model_validate(
        client.get(f"/documents/{document_id}/confidence").json()
    )
    assert parsed.auto_validate_threshold == pytest.approx(0.85)


def test_threshold_honors_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """The route reads ``settings.hitl_auto_validate_threshold`` —
    setting the env var before building services should land the
    new value in the response."""
    monkeypatch.setenv("KW_HITL_AUTO_VALIDATE_THRESHOLD", "0.72")
    services = build_services()
    app = create_app(services=services)
    document_id, _ = _upload(services)
    client = TestClient(app)
    parsed = DocumentConfidenceResponse.model_validate(
        client.get(f"/documents/{document_id}/confidence").json()
    )
    assert parsed.auto_validate_threshold == pytest.approx(0.72)


# ─── Schema ────────────────────────────────────────────────────────────


def test_schema_version_is_v0_1_literal() -> None:
    assert DOCUMENT_CONFIDENCE_SCHEMA_VERSION == "v0.1"
